"""Формування тексту звітів: щоденний дайджест, тижневий звіт, звіт на вимогу."""
from datetime import datetime, timedelta

from config import CATEGORIES
from bot import database as db
from bot import planner_db as pdb
from bot.utils import now, format_task_compact, esc

_WEEKDAY = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
_WEEKDAY_FULL = [
    "понеділок", "вівторок", "середа", "четвер", "п'ятниця", "субота", "неділя"
]


def _q(task: dict, with_date: bool = True) -> str:
    """Одне завдання — окремою цитатою (рядок)."""
    return f"<blockquote>{format_task_compact(task, with_date)}</blockquote>"


def _by_day_quotes(tasks: list[dict]) -> list[str]:
    """Групує по даті дедлайну: дата заголовком, під нею — кожне завдання цитатою."""
    by_day: dict[str, list[dict]] = {}
    for t in tasks:
        d = datetime.fromisoformat(t["deadline"])
        by_day.setdefault(d.strftime("%Y-%m-%d"), []).append(t)
    out = []
    for key in sorted(by_day):
        d = datetime.fromisoformat(key)
        out.append(f"<b>{_WEEKDAY[d.weekday()]} {d.strftime('%d.%m')}</b>")
        out += [_q(t, with_date=False) for t in by_day[key]]
    return out


def render_grouped(tasks: list[dict]) -> list[str]:
    """Завдання по категоріях, усередині — поділ з дедлайном / без; кожне цитатою."""
    out: list[str] = []
    groups: dict[str, list[dict]] = {}
    for t in tasks:
        groups.setdefault(t.get("category", "personal"), []).append(t)
    for cat, label in CATEGORIES.items():
        items = groups.get(cat, [])
        if not items:
            continue
        out.append(f"\n<b>{label}</b>")
        dated = [t for t in items if t.get("deadline")]
        undated = [t for t in items if not t.get("deadline")]
        if dated and undated:
            out.append("📆 <i>з дедлайном:</i>")
            out += [_q(t, True) for t in dated]
            out.append("📌 <i>без дедлайну:</i>")
            out += [_q(t, False) for t in undated]
        else:
            out += [_q(t, bool(dated)) for t in items]
    return out


async def _schedule_section(day: str) -> list[str]:
    """Day schedule + today's habits for the morning digest. Never breaks the digest."""
    out: list[str] = []
    try:
        blocks = await pdb.day_blocks(day)
        habits = [h for h in await pdb.habits_for_day(day) if h["scheduled"]]
    except Exception:  # noqa: BLE001
        return out
    if blocks:
        out.append("🗓 <b>Розклад на сьогодні:</b>")
        rows = [
            f"<code>{b['start']}–{b['end']}</code> {esc(b['emoji'])} {esc(b['title'])}".replace("  ", " ")
            for b in blocks
        ]
        out.append("<blockquote>" + "\n".join(rows) + "</blockquote>")
        out.append("")
    if habits:
        items = []
        for h in habits:
            streak = f" 🔥{h['streak']}" if h["streak"] >= 2 else ""
            items.append(f"{esc(h['emoji'])} {esc(h['title'])}{streak}".strip())
        out.append("🔁 <b>Звички:</b> " + " · ".join(items))
        out.append("")
    return out


async def build_daily_digest() -> str:
    """Зранку, з поділом: прострочені → сьогодні → далі будуть → без дедлайну."""
    n = now()
    overdue = await db.list_overdue()
    today_all = await db.list_today()
    today = [t for t in today_all if datetime.fromisoformat(t["deadline"]) >= n]
    upcoming = await db.list_upcoming()
    no_dl = await db.list_no_deadline()

    lines = [
        f"☀️ <b>Доброго ранку!</b>  {n.strftime('%d.%m')} ({_WEEKDAY_FULL[n.weekday()]})",
        "",
    ]

    lines += await _schedule_section(n.date().isoformat())

    if overdue:
        lines.append(f"🔴 <b>Прострочені ({len(overdue)}):</b>")
        lines += [_q(t) for t in overdue]
        lines.append("")

    lines.append("📅 <b>СЬОГОДНІ робити:</b>")
    lines += [_q(t) for t in today] if today else ["<blockquote>— дедлайнів на сьогодні немає</blockquote>"]
    lines.append("")

    if upcoming:
        lines.append("📆 <b>Далі будуть:</b>")
        lines += _by_day_quotes(upcoming)
        lines.append("")

    if no_dl:
        lines.append(f"📌 <b>Без дедлайну ({len(no_dl)}):</b>")
        lines += [_q(t, False) for t in no_dl]
        lines.append("")

    if not today and not overdue and not upcoming and not no_dl:
        lines.append("🎉 Усе чисто — гарного дня!")

    return "\n".join(lines).rstrip()


async def build_weekly_report() -> str:
    """Кінець тижня (Пт ввечері): що зроблено / що лишилось."""
    n = now()
    week_ago = n - timedelta(days=7)
    done = await db.list_done_since(week_ago)
    pending = await db.list_tasks(status="pending")
    overdue = await db.list_overdue()

    lines = [
        "🏁 <b>Підсумок тижня</b>",
        f"📆 {week_ago.strftime('%d.%m')} — {n.strftime('%d.%m')}",
        "",
        f"✅ Виконано за тиждень: <b>{len(done)}</b>",
    ]
    if done:
        for t in done[:20]:
            lines.append(f"  ✓ {esc(t['title'])}")

    lines.append(f"\n⏳ Лишилось у роботі: <b>{len(pending)}</b>")
    lines += render_grouped(pending)

    if overdue:
        lines.append(f"\n🔴 <b>Прострочені ({len(overdue)}):</b>")
        lines += [_q(t) for t in overdue]

    lines.append("\nГарних вихідних! 🌿")
    return "\n".join(lines)


async def build_week_ahead() -> str:
    """Понеділок зранку: що висить на найближчі 7 днів, по днях."""
    n = now()
    end = n + timedelta(days=7)
    week = await db.list_week()

    lines = [
        "🗓 <b>Огляд тижня</b>",
        f"Що висить на {n.strftime('%d.%m')} — {end.strftime('%d.%m')}",
        "",
    ]
    if not week:
        lines.append("🎉 На цей тиждень дедлайнів немає — вільно!")
        return "\n".join(lines)

    lines += _by_day_quotes(week)
    lines.append(f"\nВсього на тиждень: <b>{len(week)}</b>. Гарного тижня! 💪")
    return "\n".join(lines)


async def build_report() -> str:
    """Звіт на вимогу: повна картина."""
    pending = await db.list_tasks(status="pending")
    overdue = await db.list_overdue()
    today = await db.list_today()
    week_ago = now() - timedelta(days=7)
    done = await db.list_done_since(week_ago)

    lines = [
        "📊 <b>Звіт</b>",
        "",
        f"⏳ В роботі: <b>{len(pending)}</b>",
        f"📅 На сьогодні: <b>{len(today)}</b>",
        f"🔴 Прострочено: <b>{len(overdue)}</b>",
        f"✅ Виконано за 7 днів: <b>{len(done)}</b>",
        "",
        "<b>Активні завдання:</b>",
    ]
    lines += render_grouped(pending)
    return "\n".join(lines)


async def build_stats() -> str:
    pending = await db.list_tasks(status="pending")
    overdue = await db.list_overdue()
    today = await db.list_today()
    week = await db.list_week()
    week_ago = now() - timedelta(days=7)
    done = await db.list_done_since(week_ago)
    return (
        "📈 <b>Статистика</b>\n\n"
        f"📅 Сьогодні: {len(today)}\n"
        f"🗓 Цього тижня: {len(week)}\n"
        f"⏳ Всього в роботі: {len(pending)}\n"
        f"🔴 Прострочено: {len(overdue)}\n"
        f"✅ Виконано за 7 днів: {len(done)}"
    )
