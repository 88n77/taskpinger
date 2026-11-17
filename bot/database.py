"""Шар бази даних (SQLite через aiosqlite). CRUD над таблицею tasks."""
import os
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite

from config import DB_PATH, PRIORITY_ORDER
from bot.utils import now, add_period

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    title              TEXT NOT NULL,
    description        TEXT DEFAULT '',
    category           TEXT DEFAULT 'personal',
    priority           TEXT DEFAULT 'medium',
    deadline           TEXT,
    remind_at          TEXT,
    reminders_sent     TEXT DEFAULT '',
    custom_remind_sent INTEGER DEFAULT 0,
    status             TEXT DEFAULT 'pending',
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    recurrence         TEXT DEFAULT 'none'
);
"""


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_SQL)
        # Міграція для старих БД без колонки recurrence
        cur = await db.execute("PRAGMA table_info(tasks)")
        cols = {row[1] for row in await cur.fetchall()}
        if "recurrence" not in cols:
            await db.execute("ALTER TABLE tasks ADD COLUMN recurrence TEXT DEFAULT 'none'")
        await db.commit()


def _row_to_dict(row: aiosqlite.Row) -> dict:
    return dict(row)


async def add_task(
    title: str,
    category: str = "personal",
    priority: str = "medium",
    deadline: Optional[datetime] = None,
    remind_at: Optional[datetime] = None,
    description: str = "",
    recurrence: str = "none",
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO tasks
               (title, description, category, priority, deadline, remind_at, created_at, recurrence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                description,
                category,
                priority,
                deadline.isoformat() if deadline else None,
                remind_at.isoformat() if remind_at else None,
                now().isoformat(),
                recurrence,
            ),
        )
        await db.commit()
        return cur.lastrowid


async def get_task(task_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return _row_to_dict(row) if row else None


async def list_tasks(
    status: str = "pending",
    category: Optional[str] = None,
    priority: Optional[str] = None,
) -> list[dict]:
    """Базовий список з фільтрами по статусу/категорії/пріоритету."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if status and status != "any":
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    if priority:
        query += " AND priority = ?"
        params.append(priority)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        rows = await cur.fetchall()
    tasks = [_row_to_dict(r) for r in rows]
    return _sort_tasks(tasks)


def _sort_tasks(tasks: list[dict]) -> list[dict]:
    """Сортування: спершу з дедлайном (за зростанням), потім без; всередині — пріоритет."""
    def key(t: dict):
        has_dl = 0 if t.get("deadline") else 1
        dl = t["deadline"] if t.get("deadline") else "9999"
        prio = PRIORITY_ORDER.get(t.get("priority", "medium"), 1)
        return (has_dl, dl, prio)
    return sorted(tasks, key=key)


async def list_today() -> list[dict]:
    """Pending-таски з дедлайном на сьогодні (або без дедлайну — не входять)."""
    today = now().date()
    pending = await list_tasks(status="pending")
    res = []
    for t in pending:
        if t.get("deadline"):
            d = datetime.fromisoformat(t["deadline"]).date()
            if d == today:
                res.append(t)
    return res


async def list_no_deadline() -> list[dict]:
    """Pending-завдання без дедлайну (їх теж треба бачити в дайджесті)."""
    pending = await list_tasks(status="pending")
    return [t for t in pending if not t.get("deadline")]


async def list_upcoming() -> list[dict]:
    """Pending-завдання з дедлайном у майбутньому (після сьогодні)."""
    today = now().date()
    pending = await list_tasks(status="pending")
    return [
        t for t in pending
        if t.get("deadline") and datetime.fromisoformat(t["deadline"]).date() > today
    ]


async def list_overdue() -> list[dict]:
    """Pending-таски, у яких дедлайн вже минув."""
    n = now()
    pending = await list_tasks(status="pending")
    res = []
    for t in pending:
        if t.get("deadline"):
            if datetime.fromisoformat(t["deadline"]) < n:
                res.append(t)
    return res


async def list_week() -> list[dict]:
    """Pending-таски з дедлайном у найближчі 7 днів (включно з простроченими)."""
    n = now()
    end = n + timedelta(days=7)
    pending = await list_tasks(status="pending")
    res = []
    for t in pending:
        if t.get("deadline"):
            d = datetime.fromisoformat(t["deadline"])
            if d <= end:
                res.append(t)
    return res


async def list_done_since(since: datetime) -> list[dict]:
    """Виконані таски з completed_at >= since."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'done' AND completed_at >= ?",
            (since.isoformat(),),
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def set_status(task_id: int, status: str) -> None:
    completed = now().isoformat() if status == "done" else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
            (status, completed, task_id),
        )
        await db.commit()


async def update_field(task_id: int, field: str, value) -> None:
    allowed = {"title", "description", "category", "priority", "deadline", "remind_at", "recurrence"}
    if field not in allowed:
        raise ValueError(f"Поле {field} не можна оновлювати")
    if isinstance(value, datetime):
        value = value.isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # Скидаємо прапорці нагадувань, якщо змінили дедлайн/кастомний пінг
        if field == "deadline":
            await db.execute(
                "UPDATE tasks SET deadline = ?, reminders_sent = '' WHERE id = ?",
                (value, task_id),
            )
        elif field == "remind_at":
            await db.execute(
                "UPDATE tasks SET remind_at = ?, custom_remind_sent = 0 WHERE id = ?",
                (value, task_id),
            )
        else:
            await db.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, task_id))
        await db.commit()


async def delete_task(task_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()


async def append_reminder_sent(task_id: int, marker: str) -> None:
    """Додає позначку (поріг у хвилинах або 'overdue') до reminders_sent."""
    task = await get_task(task_id)
    if not task:
        return
    sent = [s for s in (task.get("reminders_sent") or "").split(",") if s]
    if marker not in sent:
        sent.append(marker)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET reminders_sent = ? WHERE id = ?",
            (",".join(sent), task_id),
        )
        await db.commit()


async def set_custom_remind_sent(task_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET custom_remind_sent = 1 WHERE id = ?", (task_id,)
        )
        await db.commit()


async def all_pending_with_reminders() -> list[dict]:
    """Pending-таски, у яких є дедлайн або кастомний пінг (для планувальника)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status = 'pending'
               AND (deadline IS NOT NULL OR remind_at IS NOT NULL)"""
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]


async def complete_task(task_id: int) -> Optional[int]:
    """Mark task done; for a recurring task spawn the next copy.

    Returns the id of the spawned copy (or None). Idempotent: a task that is
    already done is not completed twice, so a double tap never spawns two copies.
    """
    task = await get_task(task_id)
    if not task or task.get("status") == "done":
        return None
    await set_status(task_id, "done")
    rec = task.get("recurrence") or "none"
    if rec == "none":
        return None
    base = datetime.fromisoformat(task["deadline"]) if task.get("deadline") else now()
    next_deadline = add_period(base, rec)
    if next_deadline is None:
        return None
    return await add_task(
        title=task["title"],
        category=task.get("category", "personal"),
        priority=task.get("priority", "medium"),
        deadline=next_deadline,
        description=task.get("description", ""),
        recurrence=rec,
    )


async def reopen_task(task_id: int, spawned_id: Optional[int] = None) -> None:
    """Undo completion. If the completion spawned a recurring copy that is still
    pending, drop it so undo leaves no duplicate behind."""
    await set_status(task_id, "pending")
    if spawned_id:
        spawned = await get_task(spawned_id)
        if spawned and spawned.get("status") == "pending":
            await delete_task(spawned_id)


async def list_done_recent(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'done' ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
    return [_row_to_dict(r) for r in rows]
