from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
import aiosqlite
import json
from database import get_user, create_user, update_user_terms
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions
import config as cfg
import hmac
import hashlib
import urllib.parse
import logging
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://wsocks-mini-app.onrender.com", "https://telegram.org"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Корневой маршрут для проверки работоспособности
@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"status": "WSocks VPN API is running"}

class AuthData(BaseModel):
    init_data: str

def verify_init_data(init_data: str) -> dict:
    """
    Проверяет подлинность initData от Telegram Web Apps.
    Возвращает словарь с данными пользователя или вызывает исключение.
    """
    try:
        if not init_data:
            raise HTTPException(status_code=400, detail="init_data is empty")

        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        logger.info(f"Parsed init_data: {parsed_data}")
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            raise HTTPException(status_code=400, detail="Hash not found in init_data")

        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed_data.items()))
        secret_key = hashlib.sha256(cfg.API_TOKEN.encode()).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        logger.info(f"Computed hash: {computed_hash}, Received hash: {received_hash}")

        if computed_hash != received_hash:
            raise HTTPException(status_code=401, detail="Недействительные данные авторизации")

        user_data = urllib.parse.parse_qs(init_data).get('user', [''])[0]
        if not user_data:
            raise HTTPException(status_code=400, detail="User data not found in init_data")

        try:
            return {'user': json.loads(user_data)}  # Безопасный парсинг JSON
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            raise HTTPException(status_code=400, detail=f"Invalid user data format: {str(e)}")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Ошибка проверки initData: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка обработки initData: {str(e)}")

@app.post("/api/auth")
async def auth(data: AuthData):
    logger.info(f"Received init_data: {data.init_data}")
    try:
        user_data = verify_init_data(data.init_data)
        tg_id = user_data['user']['id']
        logger.info(f"Attempting to get user: {tg_id}")
        try:
            user = await get_user(tg_id)
        except Exception as e:
            logger.error(f"Database error in get_user: {e}")
            raise HTTPException(status_code=500, detail=f"Database error in get_user: {str(e)}")

        if not user:
            logger.info(f"Creating new user: {tg_id}")
            try:
                await create_user(tg_id)
                user = await get_user(tg_id)
            except Exception as e:
                logger.error(f"Database error in create_user: {e}")
                raise HTTPException(status_code=500, detail=f"Database error in create_user: {str(e)}")

        if not user['accepted_terms']:
            logger.info(f"Updating terms for user: {tg_id}")
            try:
                await update_user_terms(tg_id, True)  # Автоматическое принятие условий
                user['accepted_terms'] = True
            except Exception as e:
                logger.error(f"Database error in update_user_terms: {e}")
                raise HTTPException(status_code=500, detail=f"Database error in update_user_terms: {str(e)}")

        logger.info(f"Authenticated user: {tg_id}")
        return {"user": {"telegram_id": tg_id, "first_name": user_data['user'].get('first_name', '')}}
    except HTTPException as e:
        logger.error(f"HTTP error in auth: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Unexpected auth error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error in auth: {str(e)}")

@app.get("/api/subscriptions")
async def get_subscriptions(tg_id: int):
    logger.info(f"Fetching subscriptions for tg_id: {tg_id}")
    try:
        subscriptions = await get_active_subscriptions(tg_id)
        formatted_subscriptions = []
        for sub in subscriptions:
            formatted_subscriptions.append({
                "email": sub['email'],
                "panel": sub['panel'],
                "expiry_date": sub['expire'],
                "is_expired": datetime.now(timezone.utc) > datetime.strptime(sub['expire'], "%Y-%m-%d %H:%M:%S")
            })
        logger.info(f"Subscriptions fetched: {formatted_subscriptions}")
        return {"subscriptions": formatted_subscriptions}
    except Exception as e:
        logger.error(f"Ошибка получения подписок: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения подписок: {str(e)}")

# Проверка базы данных и конфигурации при старте
@app.on_event("startup")
async def startup_event():
    if not cfg.API_TOKEN:
        logger.error("API_TOKEN is not set in config.py")
        raise ValueError("API_TOKEN is not set")
    try:
        async with aiosqlite.connect("subscriptions.db") as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users_terms (
                    telegram_id INTEGER PRIMARY KEY,
                    accepted_terms BOOLEAN NOT NULL DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER,
                    email TEXT NOT NULL,
                    panel TEXT NOT NULL,
                    expire TEXT NOT NULL
                )
            """)
            await conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise e
