"""Клавіатури: reply-меню та inline-кнопки."""
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from aiogram.types import WebAppInfo

from config import CATEGORIES, PRIORITIES, RECURRENCE, WEBAPP_URL

# --- Тексти кнопок головного меню ---
BTN_ADD = "➕ Додати"
BTN_TASKS = "📋 Мої завдання"
BTN_REPORT = "📊 Звіт"
BTN_HELP = "❓ Довідка"
BTN_APP = "📱 Апка"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD), KeyboardButton(text=BTN_TASKS)],
            [KeyboardButton(text=BTN_REPORT), KeyboardButton(text=BTN_HELP)],
            [KeyboardButton(text=BTN_APP)],
        ],
        resize_keyboard=True,
    )


def category_kb(prefix: str = "setcat") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{key}")]
        for key, label in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def priority_kb(prefix: str = "setprio") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{key}")]
        for key, label in PRIORITIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recurrence_kb(prefix: str = "setrec") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}:{key}")]
        for key, label in RECURRENCE.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


WEEKDAY_NAMES = [
    "Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"
]


def weekday_kb(prefix: str = "awd") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{i}")]
        for i, name in enumerate(WEEKDAY_NAMES)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_kb(prefix: str) -> InlineKeyboardMarkup:
    """Кнопка 'Пропустити' для дедлайну у FSM."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏭ Пропустити", callback_data=f"{prefix}:skip")]]
    )


def filters_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сьогодні", callback_data="flt:today"),
                InlineKeyboardButton(text="🗓 Тиждень", callback_data="flt:week"),
            ],
            [
                InlineKeyboardButton(text="🔴 Прострочені", callback_data="flt:overdue"),
                InlineKeyboardButton(text="📋 Усі", callback_data="flt:all"),
            ],
            [
                InlineKeyboardButton(text="👤 Особисті", callback_data="flt:cat_personal"),
                InlineKeyboardButton(text="👥 Командні", callback_data="flt:cat_team"),
            ],
            [InlineKeyboardButton(text="✅ Виконані", callback_data="flt:done")],
        ]
    )


def task_actions_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готово", callback_data=f"done:{task_id}"),
                InlineKeyboardButton(text="✏️ Редагувати", callback_data=f"edit:{task_id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"del:{task_id}"),
            ]
        ]
    )


def edit_fields_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Назва", callback_data=f"ef:title:{task_id}"),
                InlineKeyboardButton(text="Дедлайн", callback_data=f"ef:deadline:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="Пріоритет", callback_data=f"ef:priority:{task_id}"),
                InlineKeyboardButton(text="Категорія", callback_data=f"ef:category:{task_id}"),
            ],
            [InlineKeyboardButton(text="🔁 Повторення", callback_data=f"ef:recurrence:{task_id}")],
        ]
    )


def confirm_delete_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 Так, видалити", callback_data=f"delyes:{task_id}"),
                InlineKeyboardButton(text="↩️ Ні", callback_data=f"delno:{task_id}"),
            ]
        ]
    )


def webapp_kb() -> InlineKeyboardMarkup:
    """Inline WebApp button: unlike a reply-keyboard WebApp button it passes signed initData."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📱 Відкрити апку", web_app=WebAppInfo(url=WEBAPP_URL))]]
    )
