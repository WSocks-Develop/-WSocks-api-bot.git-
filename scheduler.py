from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone, timedelta
import aiosqlite
import logging
from xui_utils import delete_subscriptions, PANELS

async def check_subscriptions(bot):
    try:
        async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
            cursor = await conn.cursor()
            await cursor.execute("PRAGMA journal_mode=WAL;")
            await cursor.execute("PRAGMA busy_timeout=5000;")
            await cursor.execute("SELECT tg_id, email, panel, expiry_date, warn, end FROM users")
            subscriptions = await cursor.fetchall()

            now = datetime.now(timezone.utc)
            for tg_id, email, panel, expiry_date_str, warn, end in subscriptions:
                try:
                    expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    time_left = (expiry_date - now).total_seconds()

                    if time_left <= 0 and not end:
                        await cursor.execute("UPDATE users SET end = 1 WHERE email = ?", (email,))
                        await bot.send_message(tg_id, f"⛔ Ваша подписка {email} закончилась.")
                        logging.info(f"Уведомление об истечении: {email}, tg_id={tg_id}")
                    elif 0 < time_left <= 86400 and not warn:
                        await cursor.execute("UPDATE users SET warn = 1 WHERE email = ?", (email,))
                        await bot.send_message(tg_id, f"⚠️ Ваша подписка {email} истекает через 24 часа!")
                        logging.info(f"Предупреждение: {email}, tg_id={tg_id}")

                except Exception as e:
                    logging.error(f"Ошибка обработки подписки {email}: {e}")

            await conn.commit()

    except aiosqlite.OperationalError as e:
        logging.error(f"Ошибка базы данных: {e}")
    except Exception as e:
        logging.error(f"Общая ошибка: {e}")

async def clean_expired_subscriptions():
    """Удаляет подписки, срок действия которых истёк более 7 дней назад"""
    try:
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(days=7)  # Граница в 7 дней

        for panel in PANELS:
            try:
                logging.info(f"Проверяем подписки в {panel['name']}...")
                inbounds = panel["api"].inbound.get_list()  # Предполагаем, что это синхронная функция

                for inbound in inbounds:
                    for client in inbound.settings.clients:
                        expiry_date = datetime.fromtimestamp(client.expiry_time / 1000.0, tz=timezone.utc)

                        if expiry_date < threshold and "DE-FRA-USER" in client.email:  # Проверяем, прошло ли 7+ дней
                            logging.info(f"Удаляем просроченную подписку {client.email} (истекла {expiry_date})...")
                            delete_subscriptions(panel['name'], client.email)  # Предполагаем, что это синхронная функция
                            logging.info(f"Подписка {client.email} успешно удалена.")

                        elif expiry_date < now and "DE-FRA-TRIAL" in client.email:
                            logging.info(f"Удаляем просроченную подписку {client.email} (истекла {expiry_date})...")
                            delete_subscriptions(panel['name'], client.email)  # Предполагаем, что это синхронная функция
                            logging.info(f"Подписка {client.email} успешно удалена.")

            except Exception as e:
                logging.error(f"Ошибка при обработке панели {panel['name']}: {e}")

    except Exception as e:
        logging.error(f"Ошибка в планировщике удаления подписок: {e}")

async def sync_subscriptions():
    """
    Проверяет подписки в панелях, добавляет новые в БД и обновляет expiry_date у существующих.
    """
    async with aiosqlite.connect("subs.db") as conn:
        cursor = await conn.cursor()

        for panel in PANELS:
            try:
                panel["api"].login()  # Предполагаем, что это синхронная функция
                inbounds = panel["api"].inbound.get_list()  # Предполагаем, что это синхронная функция

                for inbound in inbounds:
                    for client in inbound.settings.clients:
                        if "DE-FRA-USER" not in client.email:
                            continue  # Пропускаем ненужные подписки

                        expiry_date_panel = datetime.fromtimestamp(client.expiry_time / 1000.0, tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S")

                        await cursor.execute("SELECT expiry_date FROM users WHERE email = ?", (client.email,))
                        row = await cursor.fetchone()

                        if row is None:
                            # Добавляем подписку, если её нет в БД
                            logging.info(f"Добавляем подписку {client.email} из {panel['name']} в БД.")
                            await cursor.execute(
                                "INSERT INTO users (tg_id, email, panel, expiry_date, warn, end) VALUES (?, ?, ?, ?, ?, ?)",
                                (client.tg_id, client.email, panel["name"], expiry_date_panel, False, False)
                            )
                        elif row[0] != expiry_date_panel:
                            # Обновляем expiry_date, если отличается
                            logging.info(f"Обновляем подписку {client.email} из {panel['name']} в БД (новый expiry_date).")
                            await cursor.execute(
                                "UPDATE users SET expiry_date = ? WHERE email = ?",
                                (expiry_date_panel, client.email)
                            )

                await conn.commit()

            except Exception as e:
                logging.error(f"Ошибка при синхронизации подписок с {panel['name']}: {e}")

def setup_scheduler(bot):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, "interval", hours=3, args=(bot,))
    scheduler.add_job(clean_expired_subscriptions, "interval", hours=24)
    scheduler.add_job(sync_subscriptions, "interval", hours=24.33)
    return scheduler
