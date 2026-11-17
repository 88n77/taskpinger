"""Налаштування TaskPinger 88n77. Секрети — тільки в .env."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

# --- Час ---
TIMEZONE = "Europe/Kyiv"

# --- База даних ---
DB_PATH = os.path.join("data", "tasks.db")

# --- Нагадування "за X до дедлайну" (у хвилинах, від найбільшого) ---
DEADLINE_REMINDERS_MIN = [1440, 120]  # за 1 день і за 2 години

# --- Щоденний дайджест (зранку: що робити сьогодні) ---
DAILY_DIGEST_HOUR = 9
DAILY_DIGEST_MINUTE = 0

# --- Тижневий звіт (ввечері в кінці п'ятниці) ---
WEEKLY_REPORT_DAY = "fri"   # mon/tue/wed/thu/fri/sat/sun
WEEKLY_REPORT_HOUR = 18
WEEKLY_REPORT_MINUTE = 0

# --- Категорії (особисті / командні) ---
CATEGORIES = {
    "personal": "👤 Особисті",
    "team": "👥 Командні",
}

# --- Пріоритети ---
PRIORITIES = {
    "high": "🔴 Високий",
    "medium": "🟡 Середній",
    "low": "🟢 Низький",
}

# Порядок сортування пріоритетів (менше = вище)
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# --- Повторення завдань ---
RECURRENCE = {
    "none": "🔂 Одноразове",
    "daily": "🔁 Щодня",
    "weekly": "🔁 Щотижня",
    "monthly": "🔁 Щомісяця",
}

# --- Понеділковий огляд тижня (що висить на найближчі 7 днів) ---
WEEK_AHEAD_DAY = "mon"
WEEK_AHEAD_HOUR = 9
WEEK_AHEAD_MINUTE = 0

# --- Mini App (Telegram WebApp) ---
# Public HTTPS URL of the mini app (nginx proxies /task/ -> WEBAPP_HOST:WEBAPP_PORT)
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-domain.example/task/")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "8089"))
# How long a Telegram initData signature stays valid (seconds)
WEBAPP_AUTH_TTL = 7 * 24 * 3600
