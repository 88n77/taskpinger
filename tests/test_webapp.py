"""End-to-end check of the Mini App backend on a throwaway DB.

Run from the project root:  python tests/test_webapp.py
No pytest needed; never touches data/tasks.db.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from datetime import date, timedelta
from urllib.parse import urlencode

TOKEN = "123456:TEST-TOKEN"
OWNER = 4242
os.environ["BOT_TOKEN"] = TOKEN
os.environ["OWNER_ID"] = str(OWNER)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot import database as db  # noqa: E402
from bot import planner_db as pdb  # noqa: E402
from bot import reports  # noqa: E402
from bot.utils import now  # noqa: E402
from bot.webapp import create_app, verify_init_data  # noqa: E402

PASSED, FAILED = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    mark = "\033[32mPASS\033[0m" if cond else "\033[31mFAIL\033[0m"
    print(f"  [{mark}] {name}" + (f"  ({extra})" if extra and not cond else ""))


def init_data(user_id: int = OWNER, token: str = TOKEN, age: int = 0, tamper: bool = False) -> str:
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if tamper:
        fields["auth_date"] = str(int(fields["auth_date"]) + 1)
    return urlencode(fields)


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="tp_test_")
    db.DB_PATH = os.path.join(tmp, "tasks.db")
    await db.init_db()
    await pdb.init_planner()

    client = TestClient(TestServer(create_app()))
    await client.start_server()
    H = {"X-Init-Data": init_data()}

    async def call(method, path, body=None, headers=H, status=200):
        r = await client.request(method, path, json=body, headers=headers)
        try:
            data = await r.json()
        except Exception:  # noqa: BLE001
            data = {}
        if r.status != status:
            print(f"    ! {method} {path} -> {r.status} {data}")
        return r.status, data

    today = now().date()
    tmr = today + timedelta(days=1)
    wd = today.weekday()

    try:
        print("\n— Auth")
        check("no initData -> 401", (await call("GET", "/api/meta", headers={}, status=401))[0] == 401)
        check("tampered -> 401", (await call("GET", "/api/meta", headers={"X-Init-Data": init_data(tamper=True)}, status=401))[0] == 401)
        check("other user -> 401", (await call("GET", "/api/meta", headers={"X-Init-Data": init_data(user_id=1)}, status=401))[0] == 401)
        check("wrong token -> 401", (await call("GET", "/api/meta", headers={"X-Init-Data": init_data(token="1:x")}, status=401))[0] == 401)
        check("expired -> 401", (await call("GET", "/api/meta", headers={"X-Init-Data": init_data(age=8 * 86400)}, status=401))[0] == 401)
        st, meta = await call("GET", "/api/meta")
        check("owner -> 200 meta", st == 200 and meta["today"] == today.isoformat())
        check("verify_init_data returns user", (verify_init_data(init_data(), TOKEN) or {}).get("id") == OWNER)

        print("\n— Static")
        r = await client.get("/")
        html = await r.text()
        check("index served", r.status == 200 and "TaskPinger" in html)
        check("index no-cache", "no-cache" in r.headers.get("Cache-Control", ""))
        r = await client.get("/assets/app.js")
        check("app.js served", r.status == 200)
        check("health open", (await client.get("/health")).status == 200)

        print("\n— Tasks")
        st, r1 = await call("POST", "/api/tasks", {"title": "Звіт", "deadline": f"{today}T23:59", "priority": "high"}, status=201)
        t1 = r1.get("task", {})
        check("create with local deadline", st == 201 and t1["deadline"].startswith(f"{today}T23:59") and t1["deadline_state"] == "today")
        st, r2 = await call("POST", "/api/tasks", {"title": "Текстова дата", "deadline_text": "завтра 18:00"}, status=201)
        check("create with deadline_text", st == 201 and r2["task"]["deadline"].startswith(f"{tmr}T18:00"))
        st, _ = await call("POST", "/api/tasks", {"title": "x", "deadline_text": "абракадабра"}, status=400)
        check("bad deadline_text -> 400", st == 400)
        st, _ = await call("POST", "/api/tasks", {"title": "  "}, status=400)
        check("empty title -> 400", st == 400)
        st, _ = await call("POST", "/api/tasks", {"title": "x", "priority": "urgent"}, status=400)
        check("bad priority -> 400", st == 400)
        st, r = await call("PATCH", f"/api/tasks/{t1['id']}", {"title": "Звіт v2", "deadline": None})
        check("patch title + clear deadline", r["task"]["title"] == "Звіт v2" and r["task"]["deadline"] is None)
        st, _ = await call("PATCH", "/api/tasks/99999", {"title": "x"}, status=404)
        check("patch missing -> 404", st == 404)

        st, r = await call("POST", f"/api/tasks/{t1['id']}/done")
        check("done one-off", r["task"]["status"] == "done" and r["spawned"] is None)

        st, rr = await call("POST", "/api/tasks", {"title": "Щоденне", "deadline": f"{today}T09:00", "recurrence": "daily"}, status=201)
        rid = rr["task"]["id"]
        st, r = await call("POST", f"/api/tasks/{rid}/done")
        sp = r["spawned"]
        check("done recurring spawns next", sp is not None and sp["deadline"].startswith(f"{tmr}T09:00"))
        st, r = await call("POST", f"/api/tasks/{rid}/done")
        check("double done does not spawn twice", r["spawned"] is None)
        st, r = await call("POST", f"/api/tasks/{rid}/undo", {"spawned_id": sp["id"]})
        check("undo reopens", r["task"]["status"] == "pending")
        check("undo removes spawned copy", await db.get_task(sp["id"]) is None)
        st, r = await call("GET", "/api/tasks?status=done")
        check("done list", [t["id"] for t in r["tasks"]] == [t1["id"]])
        st, r = await call("GET", "/api/tasks")
        check("pending list", {t["title"] for t in r["tasks"]} == {"Текстова дата", "Щоденне"})
        st, _ = await call("DELETE", f"/api/tasks/{r2['task']['id']}")
        check("delete", await db.get_task(r2["task"]["id"]) is None)

        print("\n— Schedule")
        st, r = await call("POST", "/api/schedule", {"weekday": [wd, (wd + 1) % 7], "start": "7:00", "end": "08:00", "title": "Ранок", "emoji": "⏰"}, status=201)
        check("create block on 2 days", st == 201 and len(r["ids"]) == 2)
        b_today = r["ids"][0]
        st, r = await call("POST", "/api/schedule", {"weekday": wd, "start": "19:00", "end": "20:00", "title": "Спорт"}, status=201)
        b_sport = r["ids"][0]
        st, _ = await call("POST", "/api/schedule", {"weekday": wd, "start": "25:00", "end": "26:00", "title": "x"}, status=400)
        check("bad time -> 400", st == 400)
        st, _ = await call("POST", "/api/schedule", {"weekday": 9, "start": "10:00", "end": "11:00", "title": "x"}, status=400)
        check("bad weekday -> 400", st == 400)
        st, r = await call("GET", "/api/schedule")
        check("schedule grouped by weekday", len(r["days"][str(wd)]) == 2 and r["days"][str(wd)][0]["start"] == "07:00")
        st, r = await call("PATCH", f"/api/schedule/{b_sport}", {"start": "18:30"})
        check("patch block", r["block"]["start"] == "18:30")

        d = today.isoformat()
        st, _ = await call("POST", f"/api/day/{d}/override", {"block_id": b_sport, "start": "20:00", "end": "21:00"}, status=201)
        st, _ = await call("POST", f"/api/day/{d}/override", {"block_id": b_today, "skipped": True}, status=201)
        st, r = await call("POST", f"/api/day/{d}/override", {"start": "12:00", "end": "13:00", "title": "Лікар", "emoji": "🩺"}, status=201)
        extra_id = r["id"]
        st, _ = await call("POST", f"/api/day/{d}/override", {"start": "12:00", "title": "x"}, status=400)
        check("extra without end -> 400", st == 400)
        st, day = await call("GET", f"/api/day?date={d}")
        blocks = {b["key"]: b for b in day["blocks"]}
        check("skipped block flagged", blocks[f"b{b_today}"]["skipped"] is True)
        check("edited block moved", blocks[f"b{b_sport}"]["start"] == "20:00" and blocks[f"b{b_sport}"]["edited"])
        check("extra block present", blocks[f"x{extra_id}"]["title"] == "Лікар")
        check("blocks sorted", [b["start"] for b in day["blocks"]] == sorted(b["start"] for b in day["blocks"]))
        st, r = await call("POST", f"/api/day/{d}/toggle", {"key": f"b{b_sport}"})
        check("toggle block done", r["done"] is True)
        st, _ = await call("POST", f"/api/day/{d}/toggle", {"key": "drop table"}, status=400)
        check("bad key -> 400", st == 400)
        st, day2 = await call("GET", f"/api/day?date={(today + timedelta(days=7)).isoformat()}")
        check("override only affects that date", next(b for b in day2["blocks"] if b["block_id"] == b_sport)["start"] == "18:30")
        st, _ = await call("POST", f"/api/day/{d}/override", {"block_id": b_sport, "start": "21:00", "end": "22:00"}, status=201)
        st, day = await call("GET", f"/api/day?date={d}")
        check("override upsert keeps one row", sum(1 for b in day["blocks"] if b["block_id"] == b_sport) == 1)
        ov = next(b for b in day["blocks"] if b["block_id"] == b_today)["override_id"]
        await call("DELETE", f"/api/override/{ov}")
        st, day = await call("GET", f"/api/day?date={d}")
        check("reset override restores template", next(b for b in day["blocks"] if b["block_id"] == b_today)["skipped"] is False)

        target = (wd + 3) % 7
        st, r = await call("POST", "/api/schedule/copy", {"from": wd, "to": [target, wd]})
        st, sch = await call("GET", "/api/schedule")
        check("copy day (skips self)", len(sch["days"][str(target)]) == 2 and len(sch["days"][str(wd)]) == 2)
        await call("DELETE", f"/api/schedule/{b_sport}")
        st, day = await call("GET", f"/api/day?date={d}")
        check("delete block removes it + overrides", all(b["block_id"] != b_sport for b in day["blocks"]))

        print("\n— Habits")
        st, r = await call("POST", "/api/habits", {"title": "Вода", "emoji": "💧"}, status=201)
        hid = r["id"]
        st, _ = await call("POST", "/api/habits", {"title": "x", "days": "9"}, status=400)
        check("habit without valid days -> 400", st == 400)
        # backdate creation so streak history is valid
        async with __import__("aiosqlite").connect(db.DB_PATH) as c:
            await c.execute("UPDATE habits SET created_at = ? WHERE id = ?", ((now() - timedelta(days=10)).isoformat(), hid))
            await c.commit()
        for back in (1, 2, 3):
            await call("POST", f"/api/habits/{hid}/toggle", {"date": (today - timedelta(days=back)).isoformat()})
        st, r = await call("GET", "/api/habits")
        h = r["habits"][0]
        check("streak counts yesterday chain while today open", h["streak"] == 3 and h["done"] is False, str(h))
        st, r = await call("POST", f"/api/habits/{hid}/toggle", {})
        check("toggle today", r["done"] is True)
        st, r = await call("GET", "/api/habits")
        check("streak includes today", r["habits"][0]["streak"] == 4 and len(r["habits"][0]["log"]) == 4)
        st, _ = await call("POST", "/api/habits/9999/toggle", {}, status=404)
        check("toggle missing habit -> 404", st == 404)
        st, _ = await call("PATCH", f"/api/habits/{hid}", {"days": "01234"})
        st, r = await call("GET", "/api/habits")
        check("patch habit days", r["habits"][0]["days"] == "01234")

        print("\n— Streak logic")
        mon = date(2026, 11, 2)  # Monday
        created = mon - timedelta(days=14)
        weekdays_done = {(mon + timedelta(days=i)).isoformat() for i in range(5)}  # Mon-Fri
        cur, best = pdb.compute_streak("01234", weekdays_done, mon + timedelta(days=6), created)
        check("weekend not scheduled -> streak survives", cur == 5, f"{cur}")
        cur, _ = pdb.compute_streak("0123456", weekdays_done, mon + timedelta(days=6), created)
        check("daily habit broken by missed Sat", cur == 0, f"{cur}")
        cur, best = pdb.compute_streak("0123456", set(), mon, created)
        check("empty history", cur == 0 and best == 0)

        print("\n— Stats + digest")
        st, s = await call("GET", "/api/stats?days=7")
        check("stats shape", st == 200 and len(s["tasks"]) == 7 and len(s["habits"]) == 7 and len(s["schedule"]) == 7)
        check("stats counts today's done task", s["tasks"][-1]["done"] >= 1 and s["totals"]["done"] >= 1)
        check("stats habit row", s["habit_rows"][0]["streak"] >= 1)
        st, s30 = await call("GET", "/api/stats?days=30")
        check("stats 30 days", len(s30["tasks"]) == 30)
        text = await reports.build_daily_digest()
        check("digest has schedule", "Розклад на сьогодні" in text, text[:200])
        check("digest has habits", "Звички" in text)

        st, _ = await call("GET", "/api/day?date=2026-13-40", status=400)
        check("bad date -> 400", st == 400)
    finally:
        await client.close()

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
