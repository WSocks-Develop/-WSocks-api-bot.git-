from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import aiosqlite
import json
import uuid
import logging
from database import get_user, create_user, update_user_terms, add_subscription_to_db, add_payment_to_db
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions
from payments import create_payment_link, check_payment_status
import config as cfg
import hmac
import hashlib
import urllib.parse
from fastapi.middleware.cors import CORSMiddleware
from py3xui import Client
import os

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
DB_PATH = "/app/db/subscriptions.db" if os.getenv("RENDER") else "subscriptions.db"

# Корневой маршрут
@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"status": "WSocks VPN API is running"}

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
    import string
    import random
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))

def verify_init_data(init_data: str) -> dict:
    try:
        if not init_data:
            raise HTTPException(status_code=400, detail="init_data is empty")
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        logger.info(f"Parsed init_data: {parsed_data}")
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            raise HTTPException(status_code=400, detail="Hash not found in init_data")
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new("WebAppData".encode(), cfg.API_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        logger.info(f"Computed hash: {computed_hash}, Received hash: {received_hash}")
        if computed_hash != received_hash:
            raise HTTPException(status_code=401, detail="Недействительные данные авторизации")
        user_data = urllib.parse.parse_qs(init_data).get('user', [''])[0]
        if not user_data:
            raise HTTPException(status_code=400, detail="User data not found in init_data")
        try:
            return {'user': json.loads(user_data)}
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
                await update_user_terms(tg_id, True)
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
        subscriptions = get_active_subscriptions(tg_id)
        formatted_subscriptions = []
        for sub in subscriptions:
            formatted_subscriptions.append({
                "email": sub['email'],
                "panel": sub['panel'],
                "expiry_date": sub['expiry_date'].strftime("%Y-%m-%d %H:%M:%S"),
                "is_expired": sub['is_expired']
            })
        logger.info(f"Subscriptions fetched: {formatted_subscriptions}")
        return {"subscriptions": formatted_subscriptions}
    except Exception as e:
        logger.error(f"Ошибка получения подписок: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка получения подписок: {str(e)}")

@app.post("/api/buy-subscription")
async def buy_subscription(data: BuySubscriptionData):
    logger.info(f"Attempting to buy subscription for tg_id: {data.tg_id}, days: {data.days}")
    try:
        prices = {30: 89, 90: 249, 180: 449, 360: 849}
        if data.days not in prices:
            raise HTTPException(status_code=400, detail="Invalid subscription period")
        amount = prices[data.days]
        user = await get_user(data.tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        current_panel = get_best_panel()
        if not current_panel:
            raise HTTPException(status_code=500, detail="No available panels")
        label = f"{data.tg_id}-{uuid.uuid4().hex[:6]}"
        payment_link = create_payment_link(amount, label)
        email = f"DE-FRA-USER-{data.tg_id}-{uuid.uuid4().hex[:6]}"
        
        # Сохраняем временные данные в pending_payments
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("""
                INSERT INTO pending_payments (tg_id, label, days, amount, email, panel_name, created_at, is_extension)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.tg_id, label, data.days, amount, email, current_panel['name'],
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), False
            ))
            await conn.commit()
        
        logger.info(f"Payment link created for: {email}, label: {label}")
        return {
            "payment_link": payment_link,
            "label": label,
            "email": email,
            "days": data.days,
            "amount": amount
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error initiating subscription purchase: {e}")
        raise HTTPException(status_code=500, detail=f"Error initiating subscription purchase: {str(e)}")

@app.post("/api/extend-subscription")
async def extend_subscription(data: ExtendSubscriptionData):
    logger.info(f"Attempting to extend subscription for tg_id: {data.tg_id}, email: {data.email}, days: {data.days}")
    try:
        prices = {30: 89, 90: 249, 180: 449, 360: 849}
        if data.days not in prices:
            raise HTTPException(status_code=400, detail="Invalid subscription period")
        amount = prices[data.days]
        user = await get_user(data.tg_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Проверяем, существует ли подписка
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute("""
                SELECT panel, expire FROM subscriptions
                WHERE telegram_id = ? AND email = ?
            """, (data.tg_id, data.email))
            subscription = await cursor.fetchone()
            if not subscription:
                raise HTTPException(status_code=404, detail="Subscription not found")
        
        panel_name, current_expiry = subscription
        api = get_api_by_name(panel_name)
        if not api:
            raise HTTPException(status_code=500, detail="Panel not available")
        
        label = f"{data.tg_id}-{uuid.uuid4().hex[:6]}-extend"
        payment_link = create_payment_link(amount, label)
        
        # Сохраняем временные данные в pending_payments
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("""
                INSERT INTO pending_payments (tg_id, label, days, amount, email, panel_name, created_at, is_extension)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.tg_id, label, data.days, amount, data.email, panel_name,
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), True
            ))
            await conn.commit()
        
        logger.info(f"Extension payment link created for: {data.email}, label: {label}")
        return {
            "payment_link": payment_link,
            "label": label,
            "email": data.email,
            "days": data.days,
            "amount": amount
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error initiating subscription extension: {e}")
        raise HTTPException(status_code=500, detail=f"Error initiating subscription extension: {str(e)}")

@app.post("/api/confirm-payment")
async def confirm_payment(data: ConfirmPaymentData):
    logger.info(f"Confirming payment for tg_id: {data.tg_id}, label: {data.label}")
    try:
        # Проверяем статус оплаты
        if not check_payment_status(data.label):
            raise HTTPException(status_code=400, detail="Payment not confirmed")
        
        # Получаем данные из pending_payments
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute("""
                SELECT days, amount, email, panel_name, is_extension
                FROM pending_payments
                WHERE tg_id = ? AND label = ?
            """, (data.tg_id, data.label))
            payment_data = await cursor.fetchone()
            if not payment_data:
                raise HTTPException(status_code=404, detail="Pending payment not found")
            
            days, amount, email, panel_name, is_extension = payment_data
            
            api = get_api_by_name(panel_name)
            if not api:
                raise HTTPException(status_code=500, detail="Panel not available")
            
            if is_extension:
                # Продление подписки
                current_expiry = datetime.now(timezone.utc)
                async with aiosqlite.connect(DB_PATH) as conn:
                    cursor = await conn.execute("""
                        SELECT expire FROM subscriptions
                        WHERE telegram_id = ? AND email = ?
                    """, (data.tg_id, email))
                    sub_data = await cursor.fetchone()
                    if sub_data:
                        expiry_dt = datetime.strptime(sub_data[0], "%Y-%m-%d %H:%M:%S")
                        if expiry_dt > current_expiry:
                            current_expiry = expiry_dt
                
                new_expiry_time = (current_expiry + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                new_expiry_timestamp = int(datetime.strptime(new_expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
                
                # Обновляем клиента на панели
                clients = api.client.list(1)
                client = next((c for c in clients if c.email == email), None)
                if not client:
                    raise HTTPException(status_code=404, detail="Client not found on panel")
                
                client.expiry_time = new_expiry_timestamp
                api.client.update(1, client)
                
                # Обновляем подписку в базе
                async with aiosqlite.connect(DB_PATH) as conn:
                    await conn.execute("""
                        UPDATE subscriptions
                        SET expire = ?
                        WHERE telegram_id = ? AND email = ?
                    """, (new_expiry_time, data.tg_id, email))
                    await conn.commit()
                
                logger.info(f"Subscription extended: {email}, new expiry: {new_expiry_time}")
            else:
                # Новая подписка
                subscription_id = generate_sub(16)
                expiry_time = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                new_client = Client(
                    id=str(uuid.uuid4()),
                    enable=True,
                    tg_id=data.tg_id,
                    expiry_time=int(datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000),
                    flow="xtls-rprx-vision",
                    email=email,
                    sub_id=subscription_id,
                    limit_ip=5
                )
                
                # Добавляем клиента на панель
                api.client.add(1, [new_client])
                
                # Сохраняем подписку в базе
                await add_subscription_to_db(data.tg_id, email, panel_name, expiry_time)
                
                logger.info(f"Subscription created: {email}")
            
            # Сохраняем платёж в базе
            await add_payment_to_db(
                data.tg_id, data.label, "покупка" if not is_extension else "продление",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), amount, email
            )
            
            # Удаляем запись из pending_payments
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute("DELETE FROM pending_payments WHERE tg_id = ? AND label = ?", (data.tg_id, data.label))
                await conn.commit()
            
            current_panel = get_best_panel()
            if not current_panel:
                raise HTTPException(status_code=500, detail="No available panels")
            subscription_key = current_panel["create_key"](client if is_extension else new_client)
            return {
                "success": True,
                "email": email,
                "subscription_key": subscription_key,
                "expiry_date": new_expiry_time if is_extension else expiry_time
            }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error confirming payment: {e}")
        raise HTTPException(status_code=500, detail=f"Error confirming payment: {str(e)}")

@app.delete("/api/cancel-payment")
async def cancel_payment(data: ConfirmPaymentData):
    logger.info(f"Cancelling payment for tg_id: {data.tg_id}, label: {data.label}")
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute(
                "DELETE FROM pending_payments WHERE tg_id = ? AND label = ?",
                (data.tg_id, data.label)
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Pending payment not found")
            await conn.commit()
        logger.info(f"Payment cancelled: {data.label}")
        return {"success": True}
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error cancelling payment: {e}")
        raise HTTPException(status_code=500, detail=f"Error cancelling payment: {str(e)}")

@app.on_event("startup")
async def startup_event():
    if not cfg.API_TOKEN:
        logger.error("API_TOKEN is not set in config.py")
        raise ValueError("API_TOKEN is not set")
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
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
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER,
                    label TEXT NOT NULL,
                    days INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    email TEXT NOT NULL,
                    panel_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_extension BOOLEAN NOT NULL DEFAULT 0
                )
            """)
            await conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise e
