"""Voice messages → tasks: Gemini transcribes the audio and extracts tasks in one call.

The model gets today's date (Kyiv) so "завтра", "в понеділок після обіду" become concrete
datetimes; the result still goes through parse_deadline, the same as typed deadlines.
"""
import base64
import json
import os
from datetime import datetime
from typing import Optional

import aiohttp

from bot.utils import now, parse_deadline
from utils.logger import log

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODELS = [os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.0-flash")]
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

WEEKDAYS = ["понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя"]

PROMPT = """Ти — асистент таск-менеджера. Це голосове повідомлення українською (може бути суржик/російська).
Сьогодні {today}, {weekday}, зараз {time} за Києвом.

1. Дослівно розшифруй мову в "transcript".
2. Виділи всі завдання, які людина хоче собі поставити. Для кожного:
   - "title": коротке формулювання дії (з великої літери, без слів «нагадай», «треба», «додай задачу»),
   - "deadline": "YYYY-MM-DD HH:MM" за Києвом або null, якщо часу не сказано.
     Якщо названо лише день — 09:00. «Вранці» 09:00, «в обід» 13:00, «після обіду» 15:00, «ввечері» 19:00.
     Якщо названо лише час і він уже минув сьогодні — завтра,
   - "priority": "high" якщо «терміново/важливо/горить», "low" якщо «колись/не горить», інакше "medium",
   - "category": "team" якщо це про роботу з командою/клієнтом/замовленням, інакше "personal",
   - "recurrence": "daily" | "weekly" | "monthly" | "none" («щодня», «кожного понеділка» тощо).
Якщо завдань немає (просто думка) — поверни порожній список tasks.
Відповідай ТІЛЬКИ JSON: {{"transcript": "...", "tasks": [...]}}"""


class VoiceError(Exception):
    pass


async def transcribe_tasks(audio: bytes, mime: str = "audio/ogg") -> dict:
    if not GEMINI_API_KEY:
        raise VoiceError("GEMINI_API_KEY не задано в .env")
    t = now()
    body = {
        "contents": [{
            "parts": [
                {"text": PROMPT.format(today=t.strftime("%Y-%m-%d"), weekday=WEEKDAYS[t.weekday()], time=t.strftime("%H:%M"))},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
            ]
        }],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    last_error = "unknown"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        for model in MODELS:
            try:
                async with session.post(API.format(model=model), params={"key": GEMINI_API_KEY}, json=body) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        last_error = f"{model}: HTTP {resp.status} {str(data)[:200]}"
                        log.warning(f"Gemini voice {last_error}")
                        continue
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result = json.loads(text)
                result.setdefault("tasks", [])
                return result
            except (aiohttp.ClientError, KeyError, IndexError, json.JSONDecodeError, TimeoutError) as exc:
                last_error = f"{model}: {type(exc).__name__}"
                log.warning(f"Gemini voice {last_error}")
    raise VoiceError(last_error)


def normalize(task: dict) -> Optional[dict]:
    """Model output → arguments for db.add_task (validated, unknown values fall back to defaults)."""
    title = str(task.get("title") or "").strip()
    if not title:
        return None
    deadline: Optional[datetime] = None
    if task.get("deadline"):
        deadline = parse_deadline(str(task["deadline"]))
    priority = task.get("priority") if task.get("priority") in ("high", "medium", "low") else "medium"
    category = task.get("category") if task.get("category") in ("personal", "team") else "personal"
    recurrence = task.get("recurrence") if task.get("recurrence") in ("daily", "weekly", "monthly") else "none"
    if recurrence != "none" and deadline is None:
        recurrence = "none"  # a repeating task needs a first date; keep it one-off rather than guess
    return {"title": title[:200], "deadline": deadline, "priority": priority, "category": category, "recurrence": recurrence}
