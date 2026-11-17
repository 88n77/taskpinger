"""Утиліти: час, парсинг дедлайну, форматування тасок."""
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from config import TIMEZONE, CATEGORIES, PRIORITIES, RECURRENCE, DEADLINE_REMINDERS_MIN

TZ = ZoneInfo(TIMEZONE)


def now() -> datetime:
    """Поточний час у таймзоні користувача."""
    return datetime.now(TZ)


# --- Парсинг дедлайну ---------------------------------------------------------

WEEKDAYS = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "нд": 6,
    "понеділок": 0, "вівторок": 1, "середа": 2, "четвер": 3,
    "пʼятниця": 4, "п'ятниця": 4, "пятниця": 4, "субота": 5, "неділя": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_REL_UNITS = {
    "хв": "minutes", "min": "minutes", "m": "minutes", "хвилин": "minutes",
    "год": "hours", "h": "hours", "г": "hours", "години": "hours", "годин": "hours",
    "дн": "days", "d": "days", "день": "days", "дня": "days", "днів": "days", "д": "days",
    "тиж": "weeks", "w": "weeks", "т": "weeks", "тижні": "weeks", "тижнів": "weeks",
}

_SKIP_WORDS = {"-", "пропустити", "skip", "ні", "нема", "немає", "no", "0"}

# Стеми назв місяців (укр + англ) → номер місяця. Збіг за початком слова.
_MONTHS = {
    "січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6,
    "лип": 7, "серп": 8, "вер": 9, "жовт": 10, "листоп": 11, "груд": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _match_month(token: str):
    for stem, num in _MONTHS.items():
        if token.startswith(stem):
            return num
    return None


def _extract_time(s: str, default_h: int = 9, default_m: int = 0) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})[:.](\d{2})", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return default_h, default_m


def parse_deadline(text: str) -> Optional[datetime]:
    """Парсить рядок у datetime (tz-aware). Повертає None якщо пропуск/порожньо.

    Підтримує:
      - 2026-06-05 18:00 / 2026-06-05
      - 05.06.2026 18:00 / 05.06 18:00
      - завтра 18:00 / сьогодні / післязавтра
      - пн / пт 09:00 (найближчий такий день тижня)
      - +3h / +2d / +1w / +30m
      - 18:00 (сьогодні, або завтра якщо час уже минув)
    """
    if not text:
        return None
    t = text.strip().lower()
    if t in _SKIP_WORDS:
        return None

    n = now()

    # Відносний: +3h, +2d, +1w, +30m
    m = re.fullmatch(r"\+\s*(\d+)\s*([a-zа-яʼ']+)", t)
    if m:
        amount = int(m.group(1))
        unit_raw = m.group(2)
        unit = _REL_UNITS.get(unit_raw)
        if unit is None:
            # пробуємо за першими літерами
            for k, v in _REL_UNITS.items():
                if unit_raw.startswith(k):
                    unit = v
                    break
        if unit:
            return n + timedelta(**{unit: amount})
        return None

    # today / tomorrow / day after tomorrow
    base_date = None
    if t.startswith("day after tomorrow") or t.startswith("післязавтра"):
        base_date = (n + timedelta(days=2)).date()
    elif t.startswith("сьогодні") or t.startswith("сьогодня") or t.startswith("today"):
        base_date = n.date()
    elif t.startswith("завтра") or t.startswith("tomorrow"):
        base_date = (n + timedelta(days=1)).date()
    if base_date is not None:
        h, mi = _extract_time(t)
        return datetime(base_date.year, base_date.month, base_date.day, h, mi, tzinfo=TZ)

    # День тижня
    first = t.split()[0]
    if first in WEEKDAYS:
        target = WEEKDAYS[first]
        h, mi = _extract_time(t)
        days_ahead = (target - n.weekday()) % 7
        candidate = (n + timedelta(days=days_ahead)).replace(
            hour=h, minute=mi, second=0, microsecond=0
        )
        if days_ahead == 0 and candidate <= n:
            candidate += timedelta(days=7)
        return candidate

    # Назва місяця: "10 червня [18:00]", "june 10", "10 june 2026 18:00"
    if any(_match_month(tok) for tok in re.split(r"[\s,]+", t)):
        h, mi = _extract_time(t)
        # прибираємо час, щоб його цифри не сприйнялись як день/рік
        t_nodate = re.sub(r"\d{1,2}[:.]\d{2}", " ", t)
        month = day = None
        year = n.year
        year_given = False
        for tok in re.split(r"[\s,]+", t_nodate):
            if not tok:
                continue
            mm = _match_month(tok)
            if mm and month is None:
                month = mm
                continue
            if tok.isdigit():
                v = int(tok)
                if v > 1900:
                    year = v
                    year_given = True
                elif day is None and 1 <= v <= 31:
                    day = v
        if month and day:
            try:
                cand = datetime(year, month, day, h, mi, tzinfo=TZ)
                if not year_given and cand < n:
                    cand = cand.replace(year=year + 1)
                return cand
            except ValueError:
                return None

    # ISO: 2026-06-05 [18:00]
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t](\d{1,2})[:.](\d{2}))?", t)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        h = int(m[4]) if m[4] else 9
        mi = int(m[5]) if m[5] else 0
        try:
            return datetime(y, mo, d, h, mi, tzinfo=TZ)
        except ValueError:
            return None

    # DD.MM[.YYYY] [HH:MM]
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?:\s+(\d{1,2})[:.](\d{2}))?", t)
    if m:
        d, mo = int(m[1]), int(m[2])
        y = int(m[3]) if m[3] else n.year
        if y < 100:
            y += 2000
        h = int(m[4]) if m[4] else 9
        mi = int(m[5]) if m[5] else 0
        try:
            cand = datetime(y, mo, d, h, mi, tzinfo=TZ)
            # якщо без року і дата вже минула — наступний рік
            if not m[3] and cand < n:
                cand = cand.replace(year=y + 1)
            return cand
        except ValueError:
            return None

    # Лише час HH:MM
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", t)
    if m:
        h, mi = int(m[1]), int(m[2])
        cand = n.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand <= n:
            cand += timedelta(days=1)
        return cand

    return None


# --- Форматування -------------------------------------------------------------

def fmt_dt(dt_str: Optional[str]) -> str:
    """ISO-рядок → людський вигляд."""
    if not dt_str:
        return "—"
    try:
        d = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return dt_str
    return d.strftime("%d.%m %H:%M")


def deadline_status(dt_str: Optional[str]) -> str:
    """Емодзі-маркер дедлайну: 🔴 прострочено / 🟠 сьогодні / 🟢 ок / порожньо."""
    if not dt_str:
        return ""
    try:
        d = datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return ""
    n = now()
    if d < n:
        return "🔴"
    if d.date() == n.date():
        return "🟠"
    return "🟢"


def priority_emoji(priority: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "🟡")


def category_label(category: str) -> str:
    return CATEGORIES.get(category, category)


def priority_label(priority: str) -> str:
    return PRIORITIES.get(priority, priority)


def recurrence_label(recurrence: str) -> str:
    return RECURRENCE.get(recurrence or "none", "🔂 Одноразове")


def next_weekday(target: int, h: int = 9, mi: int = 0) -> datetime:
    """Найближча дата заданого дня тижня (0=Пн..6=Нд) о h:mi, у таймзоні Києва."""
    n = now()
    days_ahead = (target - n.weekday()) % 7
    cand = (n + timedelta(days=days_ahead)).replace(hour=h, minute=mi, second=0, microsecond=0)
    if days_ahead == 0 and cand <= n:
        cand += timedelta(days=7)
    return cand


def add_period(dt: datetime, recurrence: str) -> Optional[datetime]:
    """Зсув дати на один період повторення. None якщо немає повторення."""
    if not dt or recurrence in (None, "none"):
        return None
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "monthly":
        import calendar
        month = dt.month + 1
        year = dt.year + (month - 1) // 12
        month = (month - 1) % 12 + 1
        day = min(dt.day, calendar.monthrange(year, month)[1])
        return dt.replace(year=year, month=month, day=day)
    return None


def format_task_line(task: dict) -> str:
    """Один рядок таски для інтерактивного списку (з датою+маркером)."""
    pe = priority_emoji(task.get("priority", "medium"))
    dl = task.get("deadline")
    dl_part = ""
    if dl:
        dl_part = f"  ⏰ {fmt_dt(dl)} {deadline_status(dl)}"
    rec = " 🔁" if task.get("recurrence") and task["recurrence"] != "none" else ""
    return f"{pe} <b>#{task['id']} {esc(task['title'])}</b>{dl_part}{rec}"


def format_task_compact(task: dict, with_date: bool = True) -> str:
    """Компактний рядок для дайджесту: жирна назва, без зайвих кружечків."""
    pe = priority_emoji(task.get("priority", "medium"))
    rec = " 🔁" if task.get("recurrence") and task["recurrence"] != "none" else ""
    base = f"{pe} <b>#{task['id']} {esc(task['title'])}</b>"
    if with_date and task.get("deadline"):
        base += f" — {fmt_dt(task['deadline'])}"
    return base + rec


def format_task_full(task: dict) -> str:
    """Детальна картка завдання. Назва — цитатою, щоб впадала в око."""
    pe = priority_emoji(task["priority"])
    lines = [
        f"<blockquote>{pe} <b>{esc(task['title'])}</b></blockquote>",
    ]
    if task.get("description"):
        lines.append(f"📝 {esc(task['description'])}")
    lines.append("")  # відступ
    lines.append(f"📂 Категорія: {category_label(task['category'])}")
    lines.append(f"⚡ Пріоритет: {priority_label(task['priority'])}")
    if task.get("deadline"):
        lines.append(f"⏰ Дедлайн: {fmt_dt(task['deadline'])} {deadline_status(task['deadline'])}")
        try:
            dl = datetime.fromisoformat(task["deadline"])
            times = [
                fmt_dt((dl - timedelta(minutes=m)).isoformat())
                for m in DEADLINE_REMINDERS_MIN
            ]
            lines.append("🔔 Нагадаю: " + " · ".join(times))
        except (ValueError, TypeError):
            pass
    if task.get("remind_at"):
        lines.append(f"🔔 Окремий пінг: {fmt_dt(task['remind_at'])}")
    if task.get("recurrence") and task["recurrence"] != "none":
        lines.append(f"🔁 Повтор: {recurrence_label(task['recurrence'])}")
    status = "✅ Виконано" if task.get("status") == "done" else "⏳ В роботі"
    lines.append(f"Статус: {status}")
    lines.append(f"🆔 <code>#{task['id']}</code>")
    return "\n".join(lines)


def esc(text: str) -> str:
    """Екранування для HTML parse_mode."""
    if text is None:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
