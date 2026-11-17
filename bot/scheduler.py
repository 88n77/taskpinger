"""Планувальник нагадувань і звітів (APScheduler)."""
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import (
    OWNER_ID,
    TIMEZONE,
    DEADLINE_REMINDERS_MIN,
    DAILY_DIGEST_HOUR,
    DAILY_DIGEST_MINUTE,
    WEEKLY_REPORT_DAY,
    WEEKLY_REPORT_HOUR,
    WEEKLY_REPORT_MINUTE,
    WEEK_AHEAD_DAY,
    WEEK_AHEAD_HOUR,
    WEEK_AHEAD_MINUTE,
)
from bot import database as db
from bot import reports
from bot.keyboards import task_actions_kb
from bot.utils import now, format_task_full, fmt_dt
from utils.logger import log


def _human_left(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440} дн."
    if minutes % 60 == 0:
        return f"{minutes // 60} год."
    return f"{minutes} хв."


async def _safe_send(bot: Bot, text: str, **kwargs) -> None:
    """Відправка з захистом від падіння + пауза проти flood control."""
    try:
        await bot.send_message(OWNER_ID, text, **kwargs)
        await asyncio.sleep(1)
    except Exception as e:  # noqa: BLE001
        log.error(f"Помилка відправки повідомлення: {e}")


async def check_reminders(bot: Bot) -> None:
    """Кожну хвилину: пороги до дедлайну, прострочка, кастомний пінг."""
    n = now()
    try:
        tasks = await db.all_pending_with_reminders()
    except Exception as e:  # noqa: BLE001
        log.error(f"check_reminders: помилка читання БД: {e}")
        return

    for t in tasks:
        sent = set(s for s in (t.get("reminders_sent") or "").split(",") if s)

        # --- Кастомний разовий пінг ---
        if t.get("remind_at") and not t.get("custom_remind_sent"):
            try:
                ra = datetime.fromisoformat(t["remind_at"])
                if n >= ra:
                    await _safe_send(
                        bot,
                        f"🔔 <b>Нагадування</b>\n\n{format_task_full(t)}",
                        reply_markup=task_actions_kb(t["id"]),
                    )
                    await db.set_custom_remind_sent(t["id"])
                    log.info(f"Кастомне нагадування → завдання #{t['id']}")
            except (ValueError, TypeError):
                pass

        # --- Пороги "за X до дедлайну" ---
        if t.get("deadline"):
            try:
                dl = datetime.fromisoformat(t["deadline"])
            except (ValueError, TypeError):
                continue

            for threshold in DEADLINE_REMINDERS_MIN:
                marker = str(threshold)
                fire_at = dl - timedelta(minutes=threshold)
                if marker not in sent and fire_at <= n < dl:
                    await _safe_send(
                        bot,
                        f"⏰ <b>За {_human_left(threshold)} до дедлайну</b>\n"
                        f"(дедлайн {fmt_dt(t['deadline'])})\n\n{format_task_full(t)}",
                        reply_markup=task_actions_kb(t["id"]),
                    )
                    await db.append_reminder_sent(t["id"], marker)
                    sent.add(marker)
                    log.info(f"Нагадування за {_human_left(threshold)} → завдання #{t['id']}")

            # --- Прострочка (одноразово) ---
            if "overdue" not in sent and n >= dl:
                await _safe_send(
                    bot,
                    f"🔴 <b>Дедлайн минув!</b>\n\n{format_task_full(t)}",
                    reply_markup=task_actions_kb(t["id"]),
                )
                await db.append_reminder_sent(t["id"], "overdue")
                log.info(f"Нагадування 'прострочено' → завдання #{t['id']}")


async def send_daily_digest(bot: Bot) -> None:
    log.info("Відправляю щоденний дайджест")
    text = await reports.build_daily_digest()
    await _safe_send(bot, text)


async def send_weekly_report(bot: Bot) -> None:
    log.info("Відправляю тижневий підсумок")
    text = await reports.build_weekly_report()
    await _safe_send(bot, text)


async def send_week_ahead(bot: Bot) -> None:
    log.info("Відправляю понеділковий огляд тижня")
    text = await reports.build_week_ahead()
    await _safe_send(bot, text)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # Перевірка нагадувань — щохвилини
    scheduler.add_job(check_reminders, "interval", minutes=1, args=[bot], id="reminders")

    # Щоденний дайджест зранку
    scheduler.add_job(
        send_daily_digest,
        "cron",
        hour=DAILY_DIGEST_HOUR,
        minute=DAILY_DIGEST_MINUTE,
        args=[bot],
        id="daily_digest",
    )

    # Тижневий звіт — кінець п'ятниці
    scheduler.add_job(
        send_weekly_report,
        "cron",
        day_of_week=WEEKLY_REPORT_DAY,
        hour=WEEKLY_REPORT_HOUR,
        minute=WEEKLY_REPORT_MINUTE,
        args=[bot],
        id="weekly_report",
    )

    # Понеділковий огляд тижня (що висить на 7 днів)
    scheduler.add_job(
        send_week_ahead,
        "cron",
        day_of_week=WEEK_AHEAD_DAY,
        hour=WEEK_AHEAD_HOUR,
        minute=WEEK_AHEAD_MINUTE,
        args=[bot],
        id="week_ahead",
    )

    scheduler.start()
    log.info(
        f"Планувальник запущено: дайджест {DAILY_DIGEST_HOUR:02d}:{DAILY_DIGEST_MINUTE:02d}, "
        f"огляд тижня {WEEK_AHEAD_DAY} {WEEK_AHEAD_HOUR:02d}:{WEEK_AHEAD_MINUTE:02d}, "
        f"підсумок {WEEKLY_REPORT_DAY} {WEEKLY_REPORT_HOUR:02d}:{WEEKLY_REPORT_MINUTE:02d}"
    )
    return scheduler
