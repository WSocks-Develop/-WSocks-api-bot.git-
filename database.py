import asyncpg
import logging
from datetime import datetime, timezone

async def init_pool(dsn):
    """Инициализация пула соединений"""
    return await asyncpg.create_pool(dsn)


async def add_subscription_to_db(tg_id, email, panel, expiry_date, pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (tg_id, email, panel, expiry_date, warn, ends) VALUES ($1, $2, $3, $4, 0, 0)",
            tg_id, email, panel, expiry_date
        )
        logging.info(f"Подписка добавлена: {email}")

async def update_subscriptions_on_db(email, expiry_date, pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET expiry_date = $1, warn = FALSE, end = FALSE WHERE email = $2",
            expiry_date, email
        )
        logging.info(f"Подписка обновлена: {email}")

async def add_payment_to_db(telegram_id, label, operation_type, payment_time, amount, email, pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO payments (telegram_id, label, operation_type, payment_time, amount, email) VALUES ($1, $2, $3, $4, $5, $6)",
            telegram_id, label, operation_type, payment_time, amount, email
        )
        logging.info(f"Платёж добавлен: {email}")

async def get_user(tg_id, dsn):
    """Получение пользователя по tg_id"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            user = await conn.fetchrow(
                "SELECT * FROM users_terms WHERE telegram_id = $1", tg_id
            )
            return {'telegram_id': user['telegram_id'], 'accepted_terms': user['accepted_terms']} if user else None

async def create_user(tg_id, dsn, referrer_id=None):
    """Создание пользователя и реферала"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO users_terms (telegram_id, accepted_terms) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    tg_id, False
                )
                if referrer_id and referrer_id != tg_id:
                    try:
                        await conn.execute(
                            "INSERT INTO referrals (referrer_id, referee_id) VALUES ($1, $2)",
                            referrer_id, tg_id
                        )
                    except asyncpg.UniqueViolationError:
                        pass  # Пользователь уже реферал
                logging.info(f"Пользователь создан: tg_id={tg_id}")
                print("ок")

async def get_referrals(tg_id, dsn):
    """Получение списка рефералов"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            referrals = await conn.fetch(
                "SELECT referee_id, bonus_applied, bonus_date FROM referrals WHERE referrer_id = $1", tg_id
            )
            return [{'referee_id': ref['referee_id'], 'bonus_applied': ref['bonus_applied'], 'bonus_date': ref['bonus_date']} for ref in referrals]

async def apply_referral_bonus(referrer_id, referee_id, dsn):
    """Применение реферального бонуса"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE referrals SET bonus_applied = TRUE, bonus_date = $1 WHERE referrer_id = $2 AND referee_id = $3",
                datetime.now(timezone.utc), referrer_id, referee_id
            )
            logging.info(f"Бонус применён: referrer_id={referrer_id}, referee_id={referee_id}")

async def has_been_referred(tg_id, dsn):
    """Проверка, был ли пользователь рефералом"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM referrals WHERE referee_id = $1", tg_id
            )
            return count > 0

async def update_user_terms(tg_id, status, dsn):
    """Обновление статуса принятия условий"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE users_terms SET accepted_terms = $1 WHERE telegram_id = $2",
                status, tg_id
            )
            logging.info(f"Условия обновлены: tg_id={tg_id}, status={status}")

async def get_trial_status(tg_id, dsn):
    """Получение статуса пробного периода"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            result = await conn.fetchval(
                "SELECT status FROM trials WHERE tg_id = $1", tg_id
            )
            return result if result is not None else None

async def create_trial_user(tg_id, dsn):
    """Создание пробного пользователя"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO trials (tg_id, status) VALUES ($1, FALSE)", tg_id
            )
            logging.info(f"Пробный пользователь создан: tg_id={tg_id}")

async def activate_trial(tg_id, dsn):
    """Активация пробного периода"""
    async with asyncpg.create_pool(dsn) as pool:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE trials SET status = TRUE WHERE tg_id = $1", tg_id
            )
            logging.info(f"Пробный период активирован: tg_id={tg_id}")
