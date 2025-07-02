from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import json
import uuid
import logging
import hmac
import hashlib
import urllib.parse
from fastapi.middleware.cors import CORSMiddleware
from py3xui import Client
import random
import string
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions, PANELS
from payments import create_payment_link, check_payment_status
import config as cfg

app = FastAPI()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://wsocks-mini-app-zceh.onrender.com", "https://telegram.org"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Временное хранилище в памяти для платежей
payments = {}  # {label: {"tg_id": int, "days": int, "email": str, "amount": float, "panel_name": str}}

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

class ExtendSubscriptionData(BaseModel):
    tg_id: int
    days: int
    email: str

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
        if data.days not in [30, 90, 180, 360]:
            raise HTTPException(status_code=400, detail="Invalid subscription period")
        prices = {30: 89, 90: 249, 180: 449, 360: 849}
        amount = prices[data.days]
        email = f"DE-FRA-USER-{data.tg_id}-{uuid.uuid4().hex[:6]}"
        label = f"{data.tg_id}-{uuid.uuid4().hex[:6]}"
        payment_link = create_payment_link(amount, label)
        current_panel = get_best_panel()
        if not current_panel:
            raise HTTPException(status_code=500, detail="No available panels")
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
            "label": "1615487633",
            "amount": amount,
            "days": data.days
        }
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating subscription: {str(e)}")

@app.post("/api/extend-subscription")
async def extend_subscription_endpoint(data: ExtendSubscriptionData):
    logger.info(f"Extending subscription for tg_id: {data.tg_id}, email: {data.email}, days: {data.days}")
    try:
        if data.days not in [30, 90, 180, 360]:
            raise HTTPException(status_code=400, detail="Invalid subscription period")
        prices = {30: 89, 90: 249, 180: 449, 360: 849}
        amount = prices[data.days]
        label = f"1615487633"
        payment_link = create_payment_link(amount, label)
        subscriptions = get_active_subscriptions(data.tg_id)
        selected_sub = next((sub for sub in subscriptions if sub['email'] == data.email), None)
        if not selected_sub:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if selected_sub['email'].startswith("DE-FRA-TRIAL-"):
            raise HTTPException(status_code=400, detail="Trial subscriptions cannot be extended")
        payments[label] = {
            "tg_id": data.tg_id,
            "days": data.days,
            "email": data.email,
            "amount": amount,
            "panel_name": selected_sub['panel']
        }
        return {
            "payment_link": payment_link,
            "email": data.email,
            "panel": selected_sub['panel'],
            "label": "1615487633",
            "amount": amount,
            "days": data.days
        }
    except Exception as e:
        logger.error(f"Error extending subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Error extending subscription: {str(e)}")

@app.post("/api/confirm-payment")
async def confirm_payment(data: ConfirmPaymentData):
    logger.info(f"Confirming payment for tg_id: {data.tg_id}, label: {data.label}")
    try:
        payment = payments.get(data.label)
        current_label = data.label
        if  not payment: #payment["tg_id"] != data.tg_id or
            logger.error(f"Payment not found for label: {data.label}, tg_id: {data.tg_id}")
            raise HTTPException(status_code=404, detail="Payment not found")
        if not check_payment_status(current_label):
            logger.error(f"Payment not confirmed for label: {data.label}")
            raise HTTPException(status_code=400, detail="Payment not confirmed")
        current_panel = next((p for p in PANELS if p['name'] == payment['panel_name']), None)
        if not current_panel:
            logger.error(f"Panel not found: {payment['panel_name']}")
            raise HTTPException(status_code=500, detail="Panel not found")
        api = get_api_by_name(payment['panel_name'])
        subscriptions = get_active_subscriptions(data.tg_id)
        selected_sub = next((sub for sub in subscriptions if sub['email'] == payment['email']), None)
        if not selected_sub:
            logger.error(f"Subscription not found for email: {payment['email']}")
            raise HTTPException(status_code=404, detail="Subscription not found")
        new_expiry = None
        if not current_label.startswith("EXTEND-"):
            client_found = False
            inbounds = api.inbound.get_list()
            for inbound in inbounds:
                for client in inbound.settings.clients:
                    if client.email == payment['email'] and client.tg_id == data.tg_id:
                        new_expiry = datetime.now(timezone.utc) + timedelta(days=payment['days'])
                        client.expiry_time = int(new_expiry.timestamp() * 1000)
                        api.client.update(client.id, client)
                        client_found = True
                        break
                if client_found:
                    break
            if not client_found:
                logger.error(f"Client not found for email: {payment['email']}, tg_id: {data.tg_id}")
                raise HTTPException(status_code=404, detail="Client not found")
        else:
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
            new_expiry = expiry_time
        del payments[data.label]
        logger.info(f"Payment confirmed for tg_id: {data.tg_id}, email: {payment['email']}, new_expiry: {new_expiry}")
        return {
            "success": True,
            "email": payment['email'],
            "key": subscription_key if not payment['label'].startswith("EXTEND-") else None,
            "expiry_date": new_expiry.strftime("%Y-%m-%d %H:%M:%S"),
            "days": payment['days']
        }
    except HTTPException as e:
        logger.error(f"HTTP error in confirm_payment: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in confirm_payment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Unexpected error in confirm_payment: {str(e)}")
