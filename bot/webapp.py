"""Mini App backend: static files + JSON API, embedded in the bot process (aiohttp).

Every /api request must carry Telegram WebApp initData in the X-Init-Data header.
The signature is verified with the bot token and the user must be OWNER_ID.
"""
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl

from aiohttp import web

from config import (
    BOT_TOKEN, OWNER_ID, CATEGORIES, PRIORITIES, RECURRENCE,
    WEBAPP_AUTH_TTL, DEADLINE_REMINDERS_MIN,
)
from bot import database as db
from bot import planner_db as pdb
from bot.utils import now, parse_deadline, TZ
from utils.logger import log

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp")
WEEKDAYS_UA = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]


# --- Auth ---------------------------------------------------------------------------

def verify_init_data(init_data: str, bot_token: str, max_age: int = WEBAPP_AUTH_TTL) -> Optional[dict]:
    """Validate Telegram WebApp initData. Returns the user dict or None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        if time.time() - int(pairs.get("auth_date", "0")) > max_age:
            return None
        return json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None


@web.middleware
async def auth_and_errors(request: web.Request, handler):
    if request.path.startswith("/api/"):
        user = verify_init_data(request.headers.get("X-Init-Data", ""), BOT_TOKEN)
        if not user or int(user.get("id", 0)) != OWNER_ID:
            return web.json_response({"error": "unauthorized"}, status=401)
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, TypeError) as e:
        return web.json_response({"error": str(e) or "bad request"}, status=400)
    except KeyError as e:
        return web.json_response({"error": f"not found: {e}"}, status=404)
    except Exception as e:  # noqa: BLE001
        log.error(f"webapp {request.method} {request.path}: {e!r}")
        return web.json_response({"error": "server error"}, status=500)


# --- Helpers ------------------------------------------------------------------------

async def _body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("json object expected")
    return data


def _int(value, name: str = "id") -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"bad {name}")


def _today() -> str:
    return now().date().isoformat()


def task_out(t: dict) -> dict:
    """Task row + computed deadline state relative to server time."""
    state = None
    if t.get("deadline"):
        d = datetime.fromisoformat(t["deadline"])
        n = now()
        state = "overdue" if d < n else ("today" if d.date() == n.date() else "future")
    return {**t, "deadline_state": state}


def _parse_deadline_input(data: dict):
    """Returns (present, value). value None means clear deadline.

    Accepts 'deadline': 'YYYY-MM-DDTHH:MM' (Kyiv time) or empty, or 'deadline_text'
    in any format the bot understands ('завтра 18:00', '+2d', 'пт 09:00').
    """
    if data.get("deadline_text"):
        dl = parse_deadline(str(data["deadline_text"]))
        if dl is None:
            raise ValueError("Не зрозумів дату")
        return True, dl
    if "deadline" in data:
        raw = data["deadline"]
        if not raw:
            return True, None
        dl = datetime.fromisoformat(str(raw))
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=TZ)
        return True, dl.astimezone(TZ)
    return False, None


def _check_choice(value, allowed: dict, name: str):
    if value not in allowed:
        raise ValueError(f"bad {name}")
    return value


# --- Meta / day ---------------------------------------------------------------------

async def meta(request: web.Request):
    n = now()
    return web.json_response({
        "now": n.isoformat(),
        "today": n.date().isoformat(),
        "categories": CATEGORIES,
        "priorities": PRIORITIES,
        "recurrence": RECURRENCE,
        "reminders_min": DEADLINE_REMINDERS_MIN,
    })


async def day(request: web.Request):
    d = pdb.norm_date(request.query.get("date") or _today())
    the_day = datetime.fromisoformat(d).date()
    pending = await db.list_tasks(status="pending")
    due = [t for t in pending if t.get("deadline")
           and datetime.fromisoformat(t["deadline"]).date() == the_day]
    is_today = d == _today()
    overdue = [t for t in await db.list_overdue()
               if datetime.fromisoformat(t["deadline"]).date() < the_day] if is_today else []
    done_today = []
    if is_today:
        done_today = [t for t in await db.list_done_recent(100)
                      if t.get("completed_at")
                      and datetime.fromisoformat(t["completed_at"]).date() == the_day]
    return web.json_response({
        "date": d,
        "weekday": the_day.weekday(),
        "weekday_name": WEEKDAYS_UA[the_day.weekday()],
        "is_today": is_today,
        "now": now().isoformat(),
        "blocks": await pdb.day_blocks(d, include_skipped=True),
        "habits": await pdb.habits_for_day(d),
        "tasks": {
            "due": [task_out(t) for t in due],
            "overdue": [task_out(t) for t in overdue],
            "done": [task_out(t) for t in done_today],
        },
    })


# --- Tasks --------------------------------------------------------------------------

async def tasks_list(request: web.Request):
    status = request.query.get("status", "pending")
    if status == "done":
        rows = await db.list_done_recent(int(request.query.get("limit", 50)))
    else:
        rows = await db.list_tasks(status="pending")
    return web.json_response({"tasks": [task_out(t) for t in rows]})


async def task_create(request: web.Request):
    data = await _body(request)
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("Потрібна назва")
    _, deadline = _parse_deadline_input(data)
    task_id = await db.add_task(
        title=title[:300],
        category=_check_choice(data.get("category", "personal"), CATEGORIES, "category"),
        priority=_check_choice(data.get("priority", "medium"), PRIORITIES, "priority"),
        deadline=deadline,
        description=str(data.get("description", ""))[:2000],
        recurrence=_check_choice(data.get("recurrence", "none"), RECURRENCE, "recurrence"),
    )
    log.info(f"[app] Додано завдання #{task_id}: {title}")
    return web.json_response({"task": task_out(await db.get_task(task_id))}, status=201)


async def task_update(request: web.Request):
    task_id = _int(request.match_info["id"])
    if not await db.get_task(task_id):
        raise KeyError(task_id)
    data = await _body(request)
    if "title" in data:
        title = str(data["title"]).strip()
        if not title:
            raise ValueError("Потрібна назва")
        await db.update_field(task_id, "title", title[:300])
    if "description" in data:
        await db.update_field(task_id, "description", str(data["description"])[:2000])
    if "category" in data:
        await db.update_field(task_id, "category", _check_choice(data["category"], CATEGORIES, "category"))
    if "priority" in data:
        await db.update_field(task_id, "priority", _check_choice(data["priority"], PRIORITIES, "priority"))
    if "recurrence" in data:
        await db.update_field(task_id, "recurrence", _check_choice(data["recurrence"], RECURRENCE, "recurrence"))
    present, deadline = _parse_deadline_input(data)
    if present:
        await db.update_field(task_id, "deadline", deadline)
    log.info(f"[app] Оновлено завдання #{task_id}")
    return web.json_response({"task": task_out(await db.get_task(task_id))})


async def task_done(request: web.Request):
    task_id = _int(request.match_info["id"])
    if not await db.get_task(task_id):
        raise KeyError(task_id)
    spawned = await db.complete_task(task_id)
    log.info(f"[app] Завдання #{task_id} виконано" + (f", повтор → #{spawned}" if spawned else ""))
    return web.json_response({
        "task": task_out(await db.get_task(task_id)),
        "spawned": task_out(await db.get_task(spawned)) if spawned else None,
    })


async def task_undo(request: web.Request):
    task_id = _int(request.match_info["id"])
    if not await db.get_task(task_id):
        raise KeyError(task_id)
    data = await _body(request)
    spawned = data.get("spawned_id")
    await db.reopen_task(task_id, _int(spawned, "spawned_id") if spawned else None)
    return web.json_response({"task": task_out(await db.get_task(task_id))})


async def task_delete(request: web.Request):
    task_id = _int(request.match_info["id"])
    await db.delete_task(task_id)
    log.info(f"[app] Завдання #{task_id} видалено")
    return web.json_response({"ok": True})


# --- Schedule template -------------------------------------------------------------

async def schedule_get(request: web.Request):
    blocks = await pdb.list_blocks()
    days = {str(i): [] for i in range(7)}
    for b in blocks:
        days[str(b["weekday"])].append(b)
    return web.json_response({"days": days})


def _block_fields(data: dict) -> dict:
    out = {}
    for k in ("weekday", "start", "end", "title", "emoji", "color"):
        if k in data:
            out[k] = data[k] if k != "title" else str(data[k]).strip()[:120]
    if "title" in out and not out["title"]:
        raise ValueError("Потрібна назва")
    return out


async def schedule_create(request: web.Request):
    f = _block_fields(await _body(request))
    for k in ("weekday", "start", "end", "title"):
        if k not in f:
            raise ValueError(f"missing {k}")
    days = f.pop("weekday")
    days = days if isinstance(days, list) else [days]
    ids = [await pdb.add_block(_int(wd, "weekday"), **f) for wd in days]
    return web.json_response({"ids": ids}, status=201)


async def schedule_update(request: web.Request):
    block_id = _int(request.match_info["id"])
    if not await pdb.get_block(block_id):
        raise KeyError(block_id)
    await pdb.update_block(block_id, **_block_fields(await _body(request)))
    return web.json_response({"block": await pdb.get_block(block_id)})


async def schedule_delete(request: web.Request):
    await pdb.delete_block(_int(request.match_info["id"]))
    return web.json_response({"ok": True})


async def schedule_copy(request: web.Request):
    data = await _body(request)
    src = _int(data.get("from"), "from")
    targets = data.get("to") or []
    if not isinstance(targets, list):
        raise ValueError("to must be a list")
    n = await pdb.copy_day(src, [_int(t, "to") for t in targets])
    return web.json_response({"copied": n})


# --- Date overrides -----------------------------------------------------------------

async def override_set(request: web.Request):
    d = pdb.norm_date(request.match_info["date"])
    data = await _body(request)
    block_id = data.get("block_id")
    title = data.get("title")
    oid = await pdb.set_override(
        d,
        _int(block_id, "block_id") if block_id is not None else None,
        start=data.get("start"),
        end=data.get("end"),
        title=str(title).strip()[:120] if title else None,
        emoji=data.get("emoji"),
        color=data.get("color"),
        skipped=bool(data.get("skipped")),
    )
    return web.json_response({"id": oid}, status=201)


async def override_delete(request: web.Request):
    await pdb.delete_override(_int(request.match_info["id"]))
    return web.json_response({"ok": True})


async def block_toggle(request: web.Request):
    d = pdb.norm_date(request.match_info["date"])
    data = await _body(request)
    state = await pdb.toggle_block_done(d, str(data.get("key", "")))
    return web.json_response({"done": state})


# --- Habits -------------------------------------------------------------------------

async def habits_get(request: web.Request):
    return web.json_response({"habits": await pdb.habits_for_day(_today())})


async def habit_create(request: web.Request):
    data = await _body(request)
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValueError("Потрібна назва")
    hid = await pdb.add_habit(title[:120], str(data.get("emoji", ""))[:16], str(data.get("days", "0123456")))
    return web.json_response({"id": hid}, status=201)


async def habit_update(request: web.Request):
    hid = _int(request.match_info["id"])
    data = await _body(request)
    fields = {k: data[k] for k in ("title", "emoji", "days", "sort", "archived") if k in data}
    if "title" in fields:
        fields["title"] = str(fields["title"]).strip()[:120]
        if not fields["title"]:
            raise ValueError("Потрібна назва")
    await pdb.update_habit(hid, **fields)
    return web.json_response({"ok": True})


async def habit_delete(request: web.Request):
    await pdb.delete_habit(_int(request.match_info["id"]))
    return web.json_response({"ok": True})


async def habit_toggle(request: web.Request):
    hid = _int(request.match_info["id"])
    data = await _body(request)
    state = await pdb.toggle_habit(hid, data.get("date") or _today())
    return web.json_response({"done": state})


# --- Stats ----------------------------------------------------------------------------

async def stats(request: web.Request):
    return web.json_response(await pdb.stats(_int(request.query.get("days", 7), "days")))


# --- Static ----------------------------------------------------------------------------

async def index(request: web.Request):
    resp = web.FileResponse(os.path.join(STATIC_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


async def health(request: web.Request):
    return web.json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_and_errors], client_max_size=256 * 1024)
    r = app.router
    r.add_get("/", index)
    r.add_get("/health", health)
    r.add_static("/assets", os.path.join(STATIC_DIR, "assets"))

    r.add_get("/api/meta", meta)
    r.add_get("/api/day", day)

    r.add_get("/api/tasks", tasks_list)
    r.add_post("/api/tasks", task_create)
    r.add_patch("/api/tasks/{id}", task_update)
    r.add_post("/api/tasks/{id}/done", task_done)
    r.add_post("/api/tasks/{id}/undo", task_undo)
    r.add_delete("/api/tasks/{id}", task_delete)

    r.add_get("/api/schedule", schedule_get)
    r.add_post("/api/schedule", schedule_create)
    r.add_post("/api/schedule/copy", schedule_copy)
    r.add_patch("/api/schedule/{id}", schedule_update)
    r.add_delete("/api/schedule/{id}", schedule_delete)

    r.add_post("/api/day/{date}/override", override_set)
    r.add_post("/api/day/{date}/toggle", block_toggle)
    r.add_delete("/api/override/{id}", override_delete)

    r.add_get("/api/habits", habits_get)
    r.add_post("/api/habits", habit_create)
    r.add_patch("/api/habits/{id}", habit_update)
    r.add_delete("/api/habits/{id}", habit_delete)
    r.add_post("/api/habits/{id}/toggle", habit_toggle)

    r.add_get("/api/stats", stats)
    return app


async def start_webapp(host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(create_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info(f"Mini App сервер слухає {host}:{port}")
    return runner
