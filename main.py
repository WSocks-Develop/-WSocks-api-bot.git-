import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from scheduler import setup_scheduler
from handlers import router
from database import init_db
import config as cfg

# Настройка логирования
file_log = logging.FileHandler('py_log.log')
console_out = logging.StreamHandler()
logging.basicConfig(handlers=(file_log, console_out),
                    format='[%(asctime)s | %(levelname)s]: %(message)s',
                    datefmt='%m.%d.%Y %H:%M:%S',
                    level=logging.INFO)
logging.info('Bot started')

# Инициализация бота
bot = Bot(token=cfg.API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
dp.include_router(router)

async def main():
    try:
        scheduler = setup_scheduler(bot)
        scheduler.start()

        # Запуск бота
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        logging.error("Event loop error, running manually")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())