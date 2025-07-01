from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import json
import uuid
import logging
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions, PANELS
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

# Временное хранилище в памяти для платежей
payments = {}  # {label: {"tg_id": int, "days": int, "email": str, "amount": float}}


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


@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {"status": "OK"}


class AuthData(BaseModel):
    init_data: str


class BuySubscriptionData(BaseModel):
    tg_id: int
    days: int


class ConfirmPaymentData(BaseModel):
    tg_id: int
    label: str


@app.post("/api/auth")
async def auth(data: AuthData):
    logger.info(f"Received init_data: {data.init_data}")
    try:
        user_data = verify_init_data(data.init_data)
        tg_id = user_data['user']['id']
        first_name = user_data['user'].get('first_name', '')
        logger.info(f"Authenticated user: {tg_id}")
        return {"user": {"telegram_id": tg_id, "first_name": first_name}}
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

@app.post("/api/buy-subscription")
async def buy_subscription(data: BuySubscriptionData):
    logger.info(f"Creating subscription for tg_id: {data.tg_id}, days: {data.days}")
    try:
        # Проверка допустимых сроков подписки
        if data.days not in [30, 90, 180, 360]:
            raise HTTPException(status_code=400, detail="Invalid subscription period")

        # Цены из бота
        prices = {30: 5, 90: 5, 180: 5, 360: 5}
        amount = prices[data.days]

        # Генерация уникального email и label
        email = f"DE-FRA-USER-{data.tg_id}-{uuid.uuid4().hex[:6]}"
        label = f"{data.tg_id}-{uuid.uuid4().hex[:6]}"

        # Создание платёжной ссылки через YooMoney
        payment_link = create_payment_link(amount, label)

        # Выбор лучшей панели
        current_panel = get_best_panel()
        if not current_panel:
            raise HTTPException(status_code=500, detail="No available panels")

        # Сохранение данных платежа в памяти
        payments[label] = {
            "tg_id": data.tg_id,
            "days": data.days,
            "email": email,
            "amount": amount,
            "panel_name": current_panel['name']
        }

        return {
            "payment_link": payment_link,
            "email": email,
            "panel": current_panel['name'],
            "label": label,
            "amount": amount
        }
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating subscription: {str(e)}")


@app.post("/api/confirm-payment")
async def confirm_payment(data: ConfirmPaymentData):
    logger.info(f"Confirming payment for tg_id: {data.tg_id}, label: {data.label}")
    try:
        # Проверка существования платежа
        payment = payments.get(data.label)
        if not payment or payment["tg_id"] != data.tg_id:
            raise HTTPException(status_code=404, detail="Payment not found")

        # Проверка статуса платежа через YooMoney
        if not check_payment_status(data.label):
            raise HTTPException(status_code=400, detail="Payment not confirmed")

        # Создание подписки на панели 3XUI
        current_panel = next((p for p in PANELS if p['name'] == payment['panel_name']), None)
        if not current_panel:
            raise HTTPException(status_code=500, detail="Panel not found")

        api = get_api_by_name(payment['panel_name'])
        subscription_id = generate_sub(16)
        expiry_time = datetime.now(timezone.utc) + timedelta(days=payment['days'])

        new_client = Client(
            id=str(uuid.uuid4()),
            enable=True,
            tg_id=data.tg_id,
            expiry_time=int(expiry_time.timestamp() * 1000),
            flow="xtls-rprx-vision",
            email=payment['email'],
            sub_id=subscription_id,
            limit_ip=5
        )
        api.client.add(1, [new_client])
        subscription_key = current_panel["create_key"](new_client)

        # Удаление платежа после подтверждения
        del payments[data.label]

        return {
            "success": True,
            "email": payment['email'],
            "key": subscription_key,
            "expiry_date": expiry_time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Error confirming payment: {e}")
        raise HTTPException(status_code=500, detail=f"Error confirming payment: {str(e)}")
