from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import aiosqlite
import json
import uuid
import logging
from database import get_user, create_user, update_user_terms, add_subscription_to_db, add_payment_to_db
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions, extend_subscription
from payments import create_payment_link, check_payment_status
import config as cfg
import hmac
import hashlib
import urllib.parse
from fastapi.middleware.cors import CORSMiddleware
from py3xui import Client
import random
import string

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

# Путь к базе данных
DB_PATH = "subscriptions.db"


# Корневой маршрут
@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"status": "OK"}


class AuthData(BaseModel):
    init_data: str


class BuySubscriptionData(BaseModel):
    tg_id: int
    days: int


class ExtendSubscriptionData(BaseModel):
    tg_id: int
    email: str
    days: int


class ConfirmPaymentData(BaseModel):
    tg_id: int
    label: str


def generate_sub(length=16):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def verify_init_data(init_data: str) -> dict:
    try:
        if not init_data:
            raise HTTPException(status_code=422, detail="init_data is empty")
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        logger.info(f"Parsed init_data: {parsed_data}")
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            raise HTTPException(status_code=422, detail="Hash not found")
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new("WebAppData".encode(), cfg.API_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        logger.info(f"Computed hash: {computed_hash}, Received hash: {received_hash}")
        if computed_hash != received_hash:
            raise HTTPException(status_code=401, detail="Invalid auth data")
        user_data = urllib.parse.parse_qs(init_data).get('user', [''])[0]
        if not user_data:
            raise HTTPException(status_code=422, detail="User data not found")
        try:
            return {'user': json.loads(user_data)}
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            raise HTTPException(status_code=422, detail=f"Invalid user data format: {str(e)}")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error verifying initData: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing initData: {str(e)}")


@app.post("/api/auth")
async def auth(data: AuthData):
    logger.info(f"Received init_data: {data.init_data}")
    try:
        user_data = verify_init_data(data.init_data)
        tg_id = user_data['user']['id']
        logger.info(f"Attempting to get user: {tg_id}")
        user = await get_user(tg_id)
        if not user:
            logger.info(f"Creating new user: {tg_id}")
            await create_user(tg_id)
            user = await get_user(tg_id)
        if not user['accepted_terms']:
            logger.info(f"Updating terms for user: {tg_id}")
            await update_user_terms(tg_id, True)
            user['accepted_terms'] = True
        logger.info(f"Authenticated user: {tg_id}")
        return {"user": {"telegram_id": tg_id, "first_name": user_data['user'].get('first_name', '')}}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")


@app.get("/api/subscriptions")
async def get_subscriptions(tg_id: int):
    logger.info(f"Fetching subscriptions for tg_id: {tg_id}")
    try:
        subscriptions = get_active_subscriptions(tg_id)
        formatted_subscriptions = [
            {
                "email": sub['email'],
                "panel": sub['panel'],
                "expiry_date": sub['expiry_date'].strftime("%Y-%m-%d %H:%M:%S"),
                "is_expired": sub['is_expired']
            }
            for sub in subscriptions
        ]
        logger.info(f"Subscriptions fetched: {formatted_subscriptions}")
        return {"subscriptions": formatted_subscriptions}
    except Exception as e:
        logger.error(f"Error fetching subscriptions: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching subscriptions: {str(e)}")
