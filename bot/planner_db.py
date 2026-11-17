"""Planner storage: weekly schedule template, per-date overrides, habits, stats.

Times are stored as 'HH:MM' strings (lexicographically sortable), dates as 'YYYY-MM-DD'.
Weekday: 0=Mon .. 6=Sun (Python convention).
"""
import re
from datetime import date, datetime, timedelta
from typing import Optional

import aiosqlite

from bot import database as tasks_db
from bot.utils import now

PLANNER_SQL = """
CREATE TABLE IF NOT EXISTS schedule_blocks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    weekday    INTEGER NOT NULL,
    start      TEXT NOT NULL,
    end        TEXT NOT NULL,
    title      TEXT NOT NULL,
    emoji      TEXT DEFAULT '',
    color      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_blocks_weekday ON schedule_blocks(weekday);

-- One row per date-specific change: edit/skip of a template block (block_id set)
-- or an extra block that exists only on that date (block_id NULL).
CREATE TABLE IF NOT EXISTS schedule_overrides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT NOT NULL,
    block_id   INTEGER,
    start      TEXT,
    end        TEXT,
    title      TEXT,
    emoji      TEXT,
    color      TEXT,
    skipped    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_overrides_date ON schedule_overrides(date);

-- Block marked as done on a date. key = 'b<block_id>' or 'x<override_id>'.
CREATE TABLE IF NOT EXISTS schedule_done (
    date       TEXT NOT NULL,
    key        TEXT NOT NULL,
    done_at    TEXT NOT NULL,
    PRIMARY KEY (date, key)
);

CREATE TABLE IF NOT EXISTS habits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    emoji      TEXT DEFAULT '',
    days       TEXT DEFAULT '0123456',
    sort       INTEGER DEFAULT 0,
    archived   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS habit_log (
    habit_id   INTEGER NOT NULL,
    date       TEXT NOT NULL,
    done_at    TEXT NOT NULL,
    PRIMARY KEY (habit_id, date)
);
"""

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def db_path() -> str:
    # Read through the tasks module so tests can patch a single place
    return tasks_db.DB_PATH


async def init_planner() -> None:
    async with aiosqlite.connect(db_path()) as db:
        await db.executescript(PLANNER_SQL)
        await db.commit()


# --- Validation helpers --------------------------------------------------------

def norm_time(value: str) -> str:
    """'9:5' / '09:05' / '9.05' -> '09:05'. Raises ValueError on garbage. '24:00' allowed as end."""
    s = (value or "").strip().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", s)
    if not m:
        raise ValueError(f"bad time: {value!r}")
    h, mi = int(m[1]), int(m[2])
    if h == 24 and mi == 0:
        return "24:00"
    out = f"{h:02d}:{mi:02d}"
    if not _TIME_RE.match(out):
        raise ValueError(f"bad time: {value!r}")
    return out


def norm_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def norm_days(value: str) -> str:
    days = "".join(sorted(set(ch for ch in str(value) if ch in "0123456")))
    if not days:
        raise ValueError("habit needs at least one day")
    return days


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


# --- Weekly template -------------------------------------------------------------

async def list_blocks(weekday: Optional[int] = None) -> list[dict]:
    q, params = "SELECT * FROM schedule_blocks", []
    if weekday is not None:
        q += " WHERE weekday = ?"
        params.append(weekday)
    q += " ORDER BY weekday, start, end"
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, params)
        return _rows(await cur.fetchall())


async def get_block(block_id: int) -> Optional[dict]:
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM schedule_blocks WHERE id = ?", (block_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def add_block(weekday: int, start: str, end: str, title: str,
                    emoji: str = "", color: str = "") -> int:
    if not 0 <= int(weekday) <= 6:
        raise ValueError("weekday must be 0..6")
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute(
            "INSERT INTO schedule_blocks (weekday, start, end, title, emoji, color) VALUES (?,?,?,?,?,?)",
            (int(weekday), norm_time(start), norm_time(end), title.strip(), emoji, color),
        )
        await db.commit()
        return cur.lastrowid


async def update_block(block_id: int, **fields) -> None:
    allowed = {"weekday", "start", "end", "title", "emoji", "color"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("start", "end"):
            v = norm_time(v)
        if k == "weekday" and not 0 <= int(v) <= 6:
            raise ValueError("weekday must be 0..6")
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(block_id)
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(f"UPDATE schedule_blocks SET {', '.join(sets)} WHERE id = ?", params)
        await db.commit()


async def delete_block(block_id: int) -> None:
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM schedule_blocks WHERE id = ?", (block_id,))
        await db.execute("DELETE FROM schedule_overrides WHERE block_id = ?", (block_id,))
        await db.execute("DELETE FROM schedule_done WHERE key = ?", (f"b{block_id}",))
        await db.commit()


async def copy_day(src: int, targets: list[int]) -> int:
    """Replace the template of each target weekday with a copy of src. Returns blocks copied."""
    blocks = await list_blocks(src)
    targets = [int(t) for t in targets if 0 <= int(t) <= 6 and int(t) != src]
    async with aiosqlite.connect(db_path()) as db:
        for t in targets:
            cur = await db.execute("SELECT id FROM schedule_blocks WHERE weekday = ?", (t,))
            old_ids = [r[0] for r in await cur.fetchall()]
            for bid in old_ids:
                await db.execute("DELETE FROM schedule_overrides WHERE block_id = ?", (bid,))
            await db.execute("DELETE FROM schedule_blocks WHERE weekday = ?", (t,))
            for b in blocks:
                await db.execute(
                    "INSERT INTO schedule_blocks (weekday, start, end, title, emoji, color) VALUES (?,?,?,?,?,?)",
                    (t, b["start"], b["end"], b["title"], b["emoji"], b["color"]),
                )
        await db.commit()
    return len(blocks) * len(targets)


# --- Per-date view ----------------------------------------------------------------

async def _overrides_for(d: str) -> list[dict]:
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM schedule_overrides WHERE date = ?", (d,))
        return _rows(await cur.fetchall())


async def _done_keys(d: str) -> set[str]:
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT key FROM schedule_done WHERE date = ?", (d,))
        return {r[0] for r in await cur.fetchall()}


async def day_blocks(d: str, include_skipped: bool = False) -> list[dict]:
    """Resolved schedule for a date: weekly template + that date's overrides/extras."""
    d = norm_date(d)
    weekday = date.fromisoformat(d).weekday()
    template = await list_blocks(weekday)
    overrides = await _overrides_for(d)
    done = await _done_keys(d)
    by_block = {o["block_id"]: o for o in overrides if o["block_id"] is not None}

    out = []
    for b in template:
        o = by_block.get(b["id"])
        item = {
            "key": f"b{b['id']}", "block_id": b["id"], "override_id": None,
            "start": b["start"], "end": b["end"], "title": b["title"],
            "emoji": b["emoji"], "color": b["color"],
            "extra": False, "edited": False, "skipped": False,
        }
        if o:
            item["override_id"] = o["id"]
            if o["skipped"]:
                item["skipped"] = True
            else:
                item["edited"] = True
                for f in ("start", "end", "title", "emoji", "color"):
                    if o[f] is not None:
                        item[f] = o[f]
        if item["skipped"] and not include_skipped:
            continue
        item["done"] = item["key"] in done
        out.append(item)

    for o in overrides:
        if o["block_id"] is None:
            out.append({
                "key": f"x{o['id']}", "block_id": None, "override_id": o["id"],
                "start": o["start"], "end": o["end"], "title": o["title"],
                "emoji": o["emoji"] or "", "color": o["color"] or "",
                "extra": True, "edited": False, "skipped": False,
                "done": f"x{o['id']}" in done,
            })
    out.sort(key=lambda x: (x["start"], x["end"]))
    return out


async def set_override(d: str, block_id: Optional[int], *, start: Optional[str] = None,
                       end: Optional[str] = None, title: Optional[str] = None,
                       emoji: Optional[str] = None, color: Optional[str] = None,
                       skipped: bool = False) -> int:
    """Edit/skip a template block for one date (upsert), or add an extra block (block_id=None)."""
    d = norm_date(d)
    start = norm_time(start) if start else None
    end = norm_time(end) if end else None
    if block_id is None and not (start and end and title):
        raise ValueError("extra block needs start, end and title")
    async with aiosqlite.connect(db_path()) as db:
        if block_id is not None:
            await db.execute(
                "DELETE FROM schedule_overrides WHERE date = ? AND block_id = ?", (d, block_id)
            )
        cur = await db.execute(
            """INSERT INTO schedule_overrides (date, block_id, start, end, title, emoji, color, skipped)
               VALUES (?,?,?,?,?,?,?,?)""",
            (d, block_id, start, end, title, emoji, color, 1 if skipped else 0),
        )
        await db.commit()
        return cur.lastrowid


async def delete_override(override_id: int) -> None:
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM schedule_overrides WHERE id = ?", (override_id,))
        await db.execute("DELETE FROM schedule_done WHERE key = ?", (f"x{override_id}",))
        await db.commit()


async def toggle_block_done(d: str, key: str) -> bool:
    """Flip done state of a block on a date. Returns the new state."""
    d = norm_date(d)
    if not re.fullmatch(r"[bx]\d+", key or ""):
        raise ValueError("bad block key")
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT 1 FROM schedule_done WHERE date = ? AND key = ?", (d, key))
        if await cur.fetchone():
            await db.execute("DELETE FROM schedule_done WHERE date = ? AND key = ?", (d, key))
            state = False
        else:
            await db.execute(
                "INSERT INTO schedule_done (date, key, done_at) VALUES (?,?,?)",
                (d, key, now().isoformat()),
            )
            state = True
        await db.commit()
    return state


# --- Habits ---------------------------------------------------------------------

async def list_habits(include_archived: bool = False) -> list[dict]:
    q = "SELECT * FROM habits"
    if not include_archived:
        q += " WHERE archived = 0"
    q += " ORDER BY sort, id"
    async with aiosqlite.connect(db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q)
        return _rows(await cur.fetchall())


async def add_habit(title: str, emoji: str = "", days: str = "0123456") -> int:
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM habits")
        sort = (await cur.fetchone())[0]
        cur = await db.execute(
            "INSERT INTO habits (title, emoji, days, sort, created_at) VALUES (?,?,?,?,?)",
            (title.strip(), emoji, norm_days(days), sort, now().isoformat()),
        )
        await db.commit()
        return cur.lastrowid


async def update_habit(habit_id: int, **fields) -> None:
    allowed = {"title", "emoji", "days", "sort", "archived"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "days":
            v = norm_days(v)
        if k == "archived":
            v = 1 if v else 0
        sets.append(f"{k} = ?")
        params.append(v)
    if not sets:
        return
    params.append(habit_id)
    async with aiosqlite.connect(db_path()) as db:
        await db.execute(f"UPDATE habits SET {', '.join(sets)} WHERE id = ?", params)
        await db.commit()


async def delete_habit(habit_id: int) -> None:
    async with aiosqlite.connect(db_path()) as db:
        await db.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        await db.execute("DELETE FROM habit_log WHERE habit_id = ?", (habit_id,))
        await db.commit()


async def toggle_habit(habit_id: int, d: str) -> bool:
    d = norm_date(d)
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute("SELECT 1 FROM habits WHERE id = ?", (habit_id,))
        if not await cur.fetchone():
            raise KeyError("habit not found")
        cur = await db.execute(
            "SELECT 1 FROM habit_log WHERE habit_id = ? AND date = ?", (habit_id, d)
        )
        if await cur.fetchone():
            await db.execute("DELETE FROM habit_log WHERE habit_id = ? AND date = ?", (habit_id, d))
            state = False
        else:
            await db.execute(
                "INSERT INTO habit_log (habit_id, date, done_at) VALUES (?,?,?)",
                (habit_id, d, now().isoformat()),
            )
            state = True
        await db.commit()
    return state


async def _habit_dates(habit_id: Optional[int] = None) -> dict[int, set[str]]:
    q, params = "SELECT habit_id, date FROM habit_log", []
    if habit_id is not None:
        q += " WHERE habit_id = ?"
        params.append(habit_id)
    async with aiosqlite.connect(db_path()) as db:
        cur = await db.execute(q, params)
        out: dict[int, set[str]] = {}
        for hid, d in await cur.fetchall():
            out.setdefault(hid, set()).add(d)
        return out


def compute_streak(days: str, done_dates: set[str], today: date, created: date) -> tuple[int, int]:
    """(current, best) streak counted over scheduled days only.

    Current streak ends today if today is done, otherwise yesterday — an unfinished
    today does not break the streak until the day is over.
    """
    def scheduled(d: date) -> bool:
        return str(d.weekday()) in days

    # current
    cur = 0
    d = today
    if not (scheduled(d) and d.isoformat() in done_dates):
        d -= timedelta(days=1)
    while d >= created:
        if scheduled(d):
            if d.isoformat() in done_dates:
                cur += 1
            else:
                break
        d -= timedelta(days=1)

    # best
    best = run = 0
    d = created
    while d <= today:
        if scheduled(d):
            if d.isoformat() in done_dates:
                run += 1
                best = max(best, run)
            elif d < today:
                run = 0
        d += timedelta(days=1)
    return cur, max(best, cur)


def _created_date(h: dict, done: set[str]) -> date:
    created = datetime.fromisoformat(h["created_at"]).date()
    if done:  # logs can predate created_at if a habit was back-filled
        created = min(created, date.fromisoformat(min(done)))
    return created


async def habits_for_day(d: str) -> list[dict]:
    """Active habits with done flag for date d, scheduled flag and streaks (as of today)."""
    d = norm_date(d)
    day = date.fromisoformat(d)
    today = now().date()
    habits = await list_habits()
    logs = await _habit_dates()
    out = []
    for h in habits:
        done = logs.get(h["id"], set())
        cur, best = compute_streak(h["days"], done, today, _created_date(h, done))
        out.append({
            **h,
            "done": d in done,
            "scheduled": str(day.weekday()) in h["days"],
            "streak": cur,
            "best": best,
            "log": sorted(x for x in done if x >= (today - timedelta(days=13)).isoformat()),
        })
    return out


# --- Stats -----------------------------------------------------------------------

async def stats(days: int = 7) -> dict:
    days = max(1, min(int(days), 90))
    today = now().date()
    start = today - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]

    # tasks done per day
    done_tasks = await tasks_db.list_done_since(
        datetime(start.year, start.month, start.day, tzinfo=now().tzinfo)
    )
    tasks_per_day = {d.isoformat(): 0 for d in dates}
    for t in done_tasks:
        k = datetime.fromisoformat(t["completed_at"]).date().isoformat()
        if k in tasks_per_day:
            tasks_per_day[k] += 1

    # habits % per day
    habits = await list_habits()
    logs = await _habit_dates()
    habit_series = []
    for d in dates:
        ds = d.isoformat()
        sched = [h for h in habits
                 if str(d.weekday()) in h["days"]
                 and _created_date(h, logs.get(h["id"], set())) <= d]
        done = [h for h in sched if ds in logs.get(h["id"], set())]
        habit_series.append({"date": ds, "done": len(done), "total": len(sched)})

    # schedule adherence per day
    sched_series = []
    for d in dates:
        blocks = await day_blocks(d.isoformat())
        sched_series.append({
            "date": d.isoformat(),
            "done": sum(1 for b in blocks if b["done"]),
            "total": len(blocks),
        })

    habit_rows = []
    for h in habits:
        done = logs.get(h["id"], set())
        cur, best = compute_streak(h["days"], done, today, _created_date(h, done))
        sched_days = [d for d in dates if str(d.weekday()) in h["days"]
                      and _created_date(h, done) <= d]
        hit = sum(1 for d in sched_days if d.isoformat() in done)
        habit_rows.append({
            "id": h["id"], "title": h["title"], "emoji": h["emoji"],
            "streak": cur, "best": best, "hit": hit, "total": len(sched_days),
        })

    pending = await tasks_db.list_tasks(status="pending")
    overdue = await tasks_db.list_overdue()
    return {
        "days": days,
        "tasks": [{"date": k, "done": v} for k, v in tasks_per_day.items()],
        "habits": habit_series,
        "schedule": sched_series,
        "habit_rows": habit_rows,
        "totals": {
            "pending": len(pending),
            "overdue": len(overdue),
            "done": sum(tasks_per_day.values()),
        },
    }
