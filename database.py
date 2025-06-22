import aiosqlite
import logging
from datetime import datetime, timezone

# Настройка логирования

async def init_db():
    """Инициализация таблиц базы данных"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referrer_id INTEGER,
                referee_id INTEGER UNIQUE,
                bonus_applied INTEGER DEFAULT 0,
                bonus_date TEXT,
                FOREIGN KEY (referrer_id) REFERENCES users_terms(telegram_id),
                FOREIGN KEY (referee_id) REFERENCES users_terms(telegram_id)
            )
        """)
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER,
                email TEXT,
                panel TEXT,
                expiry_date TEXT,
                warn INTEGER DEFAULT 0,
                end INTEGER DEFAULT 0
            )
        """)
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS users_terms (
                telegram_id INTEGER PRIMARY KEY,
                accepted_terms INTEGER
            )
        """)
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS trials (
                tg_id INTEGER PRIMARY KEY,
                status INTEGER
            )
        """)
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                telegram_id INTEGER,
                label TEXT,
                operation_type TEXT,
                payment_time TEXT,
                amount REAL,
                email TEXT
            )
        """)
        await conn.commit()
        logging.info("База данных инициализирована")

async def get_user(tg_id):
    """Получение пользователя по tg_id"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT * FROM users_terms WHERE telegram_id = ?", (tg_id,))
        user = await cursor.fetchone()
        return {'telegram_id': user[0], 'accepted_terms': user[1]} if user else None

async def create_user(tg_id, referrer_id=None):
    """Создание пользователя и реферала"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute("INSERT OR IGNORE INTO users_terms (telegram_id, accepted_terms) VALUES (?, ?)", (tg_id, False))
        if referrer_id and referrer_id != tg_id:
            try:
                await cursor.execute("INSERT INTO referrals (referrer_id, referee_id) VALUES (?, ?)", (referrer_id, tg_id))
            except aiosqlite.IntegrityError:
                pass  # Пользователь уже реферал
        await conn.commit()
        logging.info(f"Пользователь создан: tg_id={tg_id}")

async def get_referrals(tg_id):
    """Получение списка рефералов"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT referee_id, bonus_applied, bonus_date FROM referrals WHERE referrer_id = ?", (tg_id,))
        referrals = await cursor.fetchall()
        return [{'referee_id': ref[0], 'bonus_applied': ref[1], 'bonus_date': ref[2]} for ref in referrals]

async def apply_referral_bonus(referrer_id, referee_id):
    """Применение реферального бонуса"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute(
            "UPDATE referrals SET bonus_applied = 1, bonus_date = ? WHERE referrer_id = ? AND referee_id = ?",
            (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), referrer_id, referee_id)
        )
        await conn.commit()
        logging.info(f"Бонус применён: referrer_id={referrer_id}, referee_id={referee_id}")

async def has_been_referred(tg_id):
    """Проверка, был ли пользователь рефералом"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT COUNT(*) FROM referrals WHERE referee_id = ?", (tg_id,))
        count = (await cursor.fetchone())[0]
        return count > 0

async def update_user_terms(tg_id, status):
    """Обновление статуса принятия условий"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute("UPDATE users_terms SET accepted_terms = ? WHERE telegram_id = ?", (status, tg_id))
        await conn.commit()
        logging.info(f"Условия обновлены: tg_id={tg_id}, status={status}")

async def get_trial_status(tg_id):
    """Получение статуса пробного периода"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("SELECT status FROM trials WHERE tg_id = ?", (tg_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def create_trial_user(tg_id):
    """Создание пробного пользователя"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute("INSERT INTO trials (tg_id, status) VALUES (?, 0)", (tg_id,))
        await conn.commit()
        logging.info(f"Пробный пользователь создан: tg_id={tg_id}")

async def activate_trial(tg_id):
    """Активация пробного периода"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute("UPDATE trials SET status = 1 WHERE tg_id = ?", (tg_id,))
        await conn.commit()
        logging.info(f"Пробный период активирован: tg_id={tg_id}")

async def add_subscription_to_db(tg_id, email, panel, expiry_date):
    """Добавление подписки"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute(
            "INSERT INTO users (tg_id, email, panel, expiry_date, warn, end) VALUES (?, ?, ?, ?, ?, ?)",
            (tg_id, email, panel, expiry_date, 0, 0)
        )
        await conn.commit()
        logging.info(f"Подписка добавлена: {email}")

async def update_subscriptions_on_db(email, expiry_date):
    """Обновление подписки"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute(
            "UPDATE users SET expiry_date = ?, warn = ?, end = ? WHERE email = ?",
            (expiry_date, 0, 0, email)
        )
        await conn.commit()
        logging.info(f"Подписка обновлена: {email}")

async def add_payment_to_db(telegram_id, label, operation_type, payment_time, amount, email):
    """Добавление платежа"""
    async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
        cursor = await conn.cursor()
        await cursor.execute("PRAGMA journal_mode=WAL;")
        await cursor.execute("PRAGMA busy_timeout=5000;")
        await cursor.execute(
            "INSERT INTO payments (telegram_id, label, operation_type, payment_time, amount, email) VALUES (?, ?, ?, ?, ?, ?)",
            (telegram_id, label, operation_type, payment_time, amount, email)
        )
        await conn.commit()
        logging.info(f"Платёж добавлен: {email}")