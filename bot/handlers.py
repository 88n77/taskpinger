"""Хендлери: команди, меню, FSM додавання/редагування, callback-и."""
import asyncio
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import OWNER_ID, CATEGORIES, PRIORITIES, RECURRENCE
from bot import database as db
from bot import reports
from bot import keyboards as kb
from bot.utils import (
    parse_deadline,
    format_task_full,
    format_task_compact,
    category_label,
    priority_label,
    add_period,
    next_weekday,
    now,
)
from utils.logger import log

router = Router()
# Доступ лише власнику
router.message.filter(F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)


class AddTask(StatesGroup):
    title = State()
    category = State()
    priority = State()
    deadline = State()
    recurrence = State()
    weekday = State()


class EditTask(StatesGroup):
    value = State()


HELP_TEXT = (
    "🤖 <b>TaskPinger</b> — твій менеджер завдань\n\n"
    "<b>Команди:</b>\n"
    "/add — додати завдання (покроково)\n"
    "/tasks — список завдань з фільтрами\n"
    "/report — звіт зараз\n"
    "/stats — коротка статистика\n"
    "/app — міні-апка: розклад дня, звички, статистика\n"
    "/cancel — скасувати поточну дію\n\n"
    "💡 <b>Швидке додавання:</b> просто напиши текст — створю завдання, далі підкоригуєш кнопками.\n\n"
    "<b>Формати дедлайну:</b>\n"
    "<code>10 червня</code>, <code>10 червня 18:00</code>, <code>10.06</code>, "
    "<code>завтра 18:00</code>, <code>пт 09:00</code>, <code>+3h</code>, <code>+2d</code>\n\n"
    "🔁 <b>Повторення:</b> одноразове / щодня / щотижня / щомісяця. Для «щотижня» без дедлайну "
    "спитаю, який день тижня. Коли тиснеш ✅ Готово — наступна копія створюється сама.\n\n"
    "🔔 Нагадування <b>виставляються автоматично</b>: зранку (9:00) дайджест на день, "
    "у понеділок (9:00) огляд тижня, за добу і за 2 год до дедлайну — пінг, "
    "а в п'ятницю ввечері (18:00) — підсумок тижня."
)


# --- /start, /help ------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привіт! 👋 Я <b>TaskPinger</b> — допоможу не губити завдання.\n\n" + HELP_TEXT,
        reply_markup=kb.main_menu(),
    )


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=kb.main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("↩️ Скасовано.", reply_markup=kb.main_menu())


@router.message(Command("app"))
@router.message(F.text == kb.BTN_APP)
async def cmd_app(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📱 <b>TaskPinger App</b> — розклад дня, звички, завдання і статистика.\n"
        "Також відкривається кнопкою <b>«📱 Апка»</b> зліва від поля вводу.",
        reply_markup=kb.webapp_kb(),
    )


# --- Перегляд завдань ---------------------------------------------------------

@router.message(Command("tasks"))
@router.message(F.text == kb.BTN_TASKS)
async def cmd_tasks(message: Message):
    tasks = await db.list_tasks(status="pending")
    await message.answer("📋 Обери фільтр або дивись весь список нижче:", reply_markup=kb.filters_kb())
    await _send_task_list(message, tasks, "📋 Усі активні")


async def _send_task_list(target: Message, tasks: list[dict], title: str):
    """Весь список — ОДНИМ повідомленням. Під ним кнопки-номери для відкриття завдання."""
    if not tasks:
        await target.answer(f"{title}\n\n— порожньо 🎉")
        return

    # Текст: спільний рендер (категорії + поділ дедлайн/без, кожне — цитатою)
    lines = [f"<b>{title}</b> ({len(tasks)}):"]
    lines += reports.render_grouped(tasks)
    lines.append("\n👇 Тисни номер, щоб відкрити завдання")

    # Кнопки-номери (по 5 у рядку)
    rows, row = [], []
    for t in tasks:
        row.append(_btn(f"#{t['id']}", f"open:{t['id']}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    await target.answer("\n".join(lines), reply_markup=_kb(rows))


@router.callback_query(F.data.startswith("open:"))
async def on_open(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    task = await db.get_task(task_id)
    if not task:
        await call.answer("Завдання не знайдено", show_alert=True)
        return
    await call.message.answer(
        format_task_full(task), reply_markup=kb.task_actions_kb(task_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("flt:"))
async def on_filter(call: CallbackQuery):
    f = call.data.split(":", 1)[1]
    await call.answer()
    m = call.message
    if f == "today":
        await _send_task_list(m, await db.list_today(), "📅 На сьогодні")
    elif f == "week":
        await _send_task_list(m, await db.list_week(), "🗓 Цього тижня")
    elif f == "overdue":
        await _send_task_list(m, await db.list_overdue(), "🔴 Прострочені")
    elif f == "all":
        await _send_task_list(m, await db.list_tasks(status="pending"), "📋 Усі активні")
    elif f == "done":
        await _send_task_list(m, await db.list_tasks(status="done"), "✅ Виконані")
    elif f == "cat_personal":
        await _send_task_list(m, await db.list_tasks(status="pending", category="personal"), "👤 Особисті")
    elif f == "cat_team":
        await _send_task_list(m, await db.list_tasks(status="pending", category="team"), "👥 Командні")


# --- Звіти --------------------------------------------------------------------

@router.message(Command("report"))
@router.message(F.text == kb.BTN_REPORT)
async def cmd_report(message: Message):
    await message.answer(await reports.build_report())


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    await message.answer(await reports.build_stats())


# --- Додавання завдання (FSM) -------------------------------------------------

@router.message(Command("add"))
@router.message(F.text == kb.BTN_ADD)
async def cmd_add(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(AddTask.title)
    await message.answer("📝 Назва завдання? (або /cancel)")


@router.message(AddTask.title, F.text)
async def add_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddTask.category)
    await message.answer("📂 Категорія:", reply_markup=kb.category_kb("acat"))


@router.callback_query(AddTask.category, F.data.startswith("acat:"))
async def add_category(call: CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[1]
    await state.update_data(category=cat)
    await state.set_state(AddTask.priority)
    await call.message.edit_text(
        f"📂 Категорія: {category_label(cat)}\n⚡ Пріоритет:",
        reply_markup=kb.priority_kb("aprio"),
    )
    await call.answer()


@router.callback_query(AddTask.priority, F.data.startswith("aprio:"))
async def add_priority(call: CallbackQuery, state: FSMContext):
    prio = call.data.split(":")[1]
    await state.update_data(priority=prio)
    await state.set_state(AddTask.deadline)
    await call.message.edit_text(
        f"⚡ Пріоритет: {priority_label(prio)}\n\n"
        "⏰ Дедлайн? Напиши (напр. <code>10 червня 18:00</code>, <code>завтра 18:00</code>, "
        "<code>+2d</code>) або тисни «Пропустити».",
        reply_markup=kb.skip_kb("adl"),
    )
    await call.answer()


@router.callback_query(AddTask.deadline, F.data == "adl:skip")
async def add_deadline_skip(call: CallbackQuery, state: FSMContext):
    await state.update_data(deadline=None)
    await _ask_recurrence(call.message, state, edit=True)
    await call.answer()


@router.message(AddTask.deadline, F.text)
async def add_deadline(message: Message, state: FSMContext):
    dl = parse_deadline(message.text)
    if dl is None:
        await message.answer(
            "🤔 Не зрозумів дату. Спробуй <code>10 червня 18:00</code>, <code>10.06</code>, "
            "<code>завтра 18:00</code>, <code>+3h</code> — або «Пропустити».",
            reply_markup=kb.skip_kb("adl"),
        )
        return
    await state.update_data(deadline=dl)
    await _ask_recurrence(message, state, edit=False)


async def _ask_recurrence(target: Message, state: FSMContext, edit: bool):
    await state.set_state(AddTask.recurrence)
    text = "🔁 Повторення? (одноразове чи регулярне)"
    if edit:
        await target.edit_text(text, reply_markup=kb.recurrence_kb("arec"))
    else:
        await target.answer(text, reply_markup=kb.recurrence_kb("arec"))


@router.callback_query(AddTask.recurrence, F.data.startswith("arec:"))
async def add_recurrence(call: CallbackQuery, state: FSMContext):
    rec = call.data.split(":")[1]
    await state.update_data(recurrence=rec)
    data = await state.get_data()
    # Щотижня без дедлайну → спитати, який день тижня
    if rec == "weekly" and not data.get("deadline"):
        await state.set_state(AddTask.weekday)
        await call.message.edit_text(
            "📅 Який день тижня повторювати?", reply_markup=kb.weekday_kb("awd")
        )
        await call.answer()
        return
    await _finish_add(call.message, state, edit=True)
    await call.answer()


@router.callback_query(AddTask.weekday, F.data.startswith("awd:"))
async def add_weekday(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split(":")[1])
    await state.update_data(deadline=next_weekday(idx, h=9, mi=0))
    await _finish_add(call.message, state, edit=True)
    await call.answer()


async def _finish_add(target: Message, state: FSMContext, edit: bool):
    data = await state.get_data()
    task_id = await db.add_task(
        title=data["title"],
        category=data.get("category", "personal"),
        priority=data.get("priority", "medium"),
        deadline=data.get("deadline"),
        recurrence=data.get("recurrence", "none"),
    )
    await state.clear()
    task = await db.get_task(task_id)
    text = "✅ <b>Завдання додано!</b>\n\n" + format_task_full(task)
    if edit:
        await target.edit_text(text, reply_markup=kb.task_actions_kb(task_id))
    else:
        await target.answer(text, reply_markup=kb.task_actions_kb(task_id))
    log.info(f"Додано завдання #{task_id}: {data['title']}")


# --- Дії над завданням --------------------------------------------------------

@router.callback_query(F.data.startswith("done:"))
async def on_done(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    task = await db.get_task(task_id)
    if task and task.get("status") == "done":
        await call.answer("Вже виконано ✅")
        return
    new_id = await db.complete_task(task_id)
    await call.answer("✅ Готово!")
    try:
        await call.message.edit_text(f"✅ <s>{call.message.html_text}</s>")
    except Exception:  # noqa: BLE001
        await call.message.edit_reply_markup(reply_markup=None)
    log.info(f"Завдання #{task_id} виконано")

    # Повторюване завдання → наступна копія вже створена в complete_task
    if new_id:
        await _announce_next_recurrence(call.message, task, new_id)


async def _announce_next_recurrence(target: Message, task: dict, new_id: int):
    rec = task["recurrence"]
    new_task = await db.get_task(new_id)
    await target.answer(
        f"🔁 <b>Повторюване завдання перенесено далі</b>\n\n" + format_task_full(new_task),
        reply_markup=kb.task_actions_kb(new_id),
    )
    log.info(f"Повторення: створено завдання #{new_id} з #{task['id']} ({rec})")


@router.callback_query(F.data.startswith("del:"))
async def on_del(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    await call.message.answer(
        f"🗑 Видалити завдання #{task_id}?", reply_markup=kb.confirm_delete_kb(task_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("delyes:"))
async def on_del_yes(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    await db.delete_task(task_id)
    await call.message.edit_text(f"🗑 Завдання #{task_id} видалено.")
    await call.answer("Видалено")
    log.info(f"Завдання #{task_id} видалено")


@router.callback_query(F.data.startswith("delno:"))
async def on_del_no(call: CallbackQuery):
    await call.message.edit_text("↩️ Скасовано.")
    await call.answer()


# --- Редагування --------------------------------------------------------------

@router.callback_query(F.data.startswith("edit:"))
async def on_edit(call: CallbackQuery):
    task_id = int(call.data.split(":")[1])
    await call.message.answer(
        f"✏️ Що змінити в завданні #{task_id}?", reply_markup=kb.edit_fields_kb(task_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("ef:"))
async def on_edit_field(call: CallbackQuery, state: FSMContext):
    _, field, task_id = call.data.split(":")
    task_id = int(task_id)

    # Пріоритет / категорія / повторення — одразу інлайн-вибором
    if field == "priority":
        rows = [
            [_btn(label, f"eset:priority:{task_id}:{key}")]
            for key, label in PRIORITIES.items()
        ]
        await call.message.edit_text("⚡ Новий пріоритет:", reply_markup=_kb(rows))
        await call.answer()
        return
    if field == "category":
        rows = [
            [_btn(label, f"eset:category:{task_id}:{key}")]
            for key, label in CATEGORIES.items()
        ]
        await call.message.edit_text("📂 Нова категорія:", reply_markup=_kb(rows))
        await call.answer()
        return
    if field == "recurrence":
        rows = [
            [_btn(label, f"eset:recurrence:{task_id}:{key}")]
            for key, label in RECURRENCE.items()
        ]
        await call.message.edit_text("🔁 Повторення:", reply_markup=_kb(rows))
        await call.answer()
        return

    # Текстові поля — через FSM
    await state.set_state(EditTask.value)
    await state.update_data(field=field, task_id=task_id)
    prompts = {
        "title": "📝 Нова назва:",
        "deadline": "⏰ Новий дедлайн (напр. <code>10 червня 18:00</code>, <code>-</code> щоб прибрати):",
    }
    await call.message.answer(prompts.get(field, "Нове значення:"))
    await call.answer()


@router.message(EditTask.value, F.text)
async def on_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field, task_id = data["field"], data["task_id"]

    if field == "deadline":
        value = parse_deadline(message.text)
        if value is None and message.text.strip().lower() not in ("-", "пропустити", "нема", "немає"):
            await message.answer("🤔 Не зрозумів дату. Спробуй ще раз або <code>-</code> щоб прибрати.")
            return
    else:
        value = message.text.strip()

    await db.update_field(task_id, field, value)
    await state.clear()
    task = await db.get_task(task_id)
    await message.answer(
        "✅ Оновлено.\n\n" + format_task_full(task),
        reply_markup=kb.task_actions_kb(task_id),
    )
    log.info(f"Завдання #{task_id} оновлено: поле {field}")


@router.callback_query(F.data.startswith("eset:"))
async def on_edit_set(call: CallbackQuery):
    _, field, task_id, value = call.data.split(":")
    await db.update_field(int(task_id), field, value)
    task = await db.get_task(int(task_id))
    await call.message.edit_text(
        "✅ Оновлено.\n\n" + format_task_full(task),
    )
    await call.message.answer("Готово ✅", reply_markup=kb.task_actions_kb(int(task_id)))
    await call.answer()
    log.info(f"Завдання #{task_id} оновлено: {field}={value}")


# --- Голосові: розшифровка + задачі через Gemini --------------------------------

@router.message(StateFilter(None), F.voice | F.audio)
async def voice_add(message: Message):
    from bot import voice

    media = message.voice or message.audio
    if media.duration and media.duration > 300:
        await message.answer("🎙 Задовге голосове (до 5 хв), розбий на частини.")
        return
    wait = await message.answer("🎙 Слухаю…")
    try:
        file = await message.bot.get_file(media.file_id)
        buf = await message.bot.download_file(file.file_path)
        result = await voice.transcribe_tasks(buf.read(), getattr(media, "mime_type", None) or "audio/ogg")
    except voice.VoiceError as exc:
        await wait.edit_text(f"🎙 Не вдалося розпізнати голосове ({exc}). Спробуй ще раз або напиши текстом.")
        log.warning(f"Голосове не розпізнано: {exc}")
        return

    import html

    transcript = html.escape(str(result.get("transcript") or "").strip())
    tasks = [t for t in (voice.normalize(x) for x in result.get("tasks", [])) if t]
    if not tasks:
        await wait.edit_text(f"🎙 Почув: <i>«{transcript or '…'}»</i>\n\nЗавдань тут не знайшов. Скажи, наприклад: «завтра о 10 подзвонити Дімону».")
        return

    await wait.edit_text(f"🎙 Почув: <i>«{transcript}»</i>\n\n✅ Додано завдань: <b>{len(tasks)}</b>")
    for t in tasks:
        task_id = await db.add_task(**t)
        task = await db.get_task(task_id)
        await message.answer(format_task_full(task), reply_markup=kb.task_actions_kb(task_id))
        log.info(f"Голосом додано завдання #{task_id}: {t['title']}")


# --- Швидке додавання (catch-all текст без стану) — РЕЄСТРУЄМО ОСТАННІМ ---------

@router.message(StateFilter(None), F.text & ~F.text.startswith("/"))
async def quick_add(message: Message):
    title = message.text.strip()
    if not title:
        return
    task_id = await db.add_task(title=title)
    task = await db.get_task(task_id)
    await message.answer(
        "⚡ <b>Швидко додано</b> (особисте, середній, без дедлайну).\n"
        "Натисни ✏️ щоб додати дедлайн/пріоритет.\n\n" + format_task_full(task),
        reply_markup=kb.task_actions_kb(task_id),
    )
    log.info(f"Швидко додано завдання #{task_id}: {title}")


# --- Дрібні хелпери для інлайн-кнопок ------------------------------------------

def _btn(text: str, data: str):
    from aiogram.types import InlineKeyboardButton
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows):
    from aiogram.types import InlineKeyboardMarkup
    return InlineKeyboardMarkup(inline_keyboard=rows)
