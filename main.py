"""TaskPinger 88n77 — точка входу. Запуск бота + планувальника."""
import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import BOT_TOKEN, OWNER_ID, WEBAPP_URL, WEBAPP_HOST, WEBAPP_PORT
from bot import database as db
from bot import planner_db
from bot.webapp import start_webapp
from bot.handlers import router
from bot.scheduler import setup_scheduler
from utils.logger import log


async def main() -> None:
    if not BOT_TOKEN or OWNER_ID == 0:
        log.error("Не задано BOT_TOKEN або OWNER_ID у .env — дивись .env.example")
        sys.exit(1)

    await db.init_db()
    await planner_db.init_planner()
    log.info("База даних готова")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    setup_scheduler(bot)

    # Mini App: HTTP server in the same process + menu button next to the input field
    web_runner = await start_webapp(WEBAPP_HOST, WEBAPP_PORT)
    if WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                chat_id=OWNER_ID,
                menu_button=MenuButtonWebApp(text="📱 Апка", web_app=WebAppInfo(url=WEBAPP_URL)),
            )
        except Exception as e:  # noqa: BLE001
            log.error(f"Не вдалось встановити кнопку Mini App: {e}")

    me = await bot.get_me()
    log.info(f"Бот @{me.username} запущено. Власник: {OWNER_ID}")
    try:
        await bot.send_message(OWNER_ID, "🟢 TaskPinger онлайн. Напиши /start")
    except Exception as e:  # noqa: BLE001
        log.error(f"Не вдалось надіслати стартове повідомлення власнику: {e}")

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Зупинено")
