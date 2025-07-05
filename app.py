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
import asyncpg
from xui_utils import get_best_panel, get_api_by_name, get_active_subscriptions, extend_subscription, PANELS
from database import add_payment_to_db, add_subscription_to_db, update_subscriptions_on_db, create_trial_user, get_trial_status
import config as cfg

app = FastAPI()
pool = None

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

@app.on_event("startup")
async def startup_event():
    global pool
    pool = await asyncpg.create_pool(
        cfg.DSN,
        min_size=2,
        max_size=5,
        max_inactive_connection_lifetime=300
    )
    logger.info("Database pool initialized")

@app.on_event("shutdown")
async def shutdown_event():
    global pool
    await pool.close()
    logger.info("Database pool closed")

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

class TrialSubscriptionData(BaseModel):
    tg_id: int

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
        current_panel = get_best_panel()
        if not current_panel:
            raise HTTPException(status_code=500, detail="No available panels")
        subscription_id = generate_sub(16)
        expiry_time = (datetime.now(timezone.utc) + timedelta(days=data.days)).strftime("%Y-%m-%d %H:%M:%S")
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
        api = get_api_by_name(current_panel['name'])
        api.client.add(1, [new_client])
        await add_subscription_to_db(str(data.tg_id), email, current_panel['name'], expiry_time, pool)
        await add_payment_to_db(str(data.tg_id), "111111", 'Покупка', expiry_time, 89, email, pool)
        subscription_key = current_panel["create_key"](new_client)
        logger.info(f"Subscription created for tg_id: {data.tg_id}, email: {email}")
        return {
            "email": email,
            "panel": current_panel['name'],
            "key": subscription_key,
            "expiry_date": expiry_time,
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
        subscriptions = get_active_subscriptions(data.tg_id)
        selected_sub = next((sub for sub in subscriptions if sub['email'] == data.email), None)
        if not selected_sub:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if selected_sub['email'].startswith("DE-FRA-TRIAL-"):
            raise HTTPException(status_code=400, detail="Trial subscriptions cannot be extended")
        api = get_api_by_name(selected_sub['panel'])
        client_found = False
        inbounds = api.inbound.get_list()
        for inbound in inbounds:
            for client in inbound.settings.clients:
                if client.email == data.email and client.tg_id == data.tg_id:
                    extend_subscription(client.email, client.id, data.days, data.tg_id, client.sub_id, api)
                    new_expiry = (datetime.now(timezone.utc) if selected_sub['is_expired'] else selected_sub['expiry_date']) + timedelta(days=data.days)
                    expiry_time = new_expiry.strftime("%Y-%m-%d %H:%M:%S")
                    await update_subscriptions_on_db(selected_sub['email'], expiry_time, pool)
                    await add_payment_to_db(str(data.tg_id), "111111", 'Продление', expiry_time, 89, selected_sub['email'], pool)
                    client_found = True
                    break
            if client_found:
                break
        if not client_found:
            raise HTTPException(status_code=404, detail="Client not found")
        logger.info(f"Subscription extended for tg_id: {data.tg_id}, email: {data.email}, new_expiry: {expiry_time}")
        return {
            "email": data.email,
            "panel": selected_sub['panel'],
            "expiry_date": expiry_time,
            "amount": {30: 89, 90: 249, 180: 449, 360: 849}[data.days],
            "days": data.days
        }
    except Exception as e:
        logger.error(f"Error extending subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Error extending subscription: {str(e)}")

@app.post("/api/activate-trial")
async def activate_trial(data: TrialSubscriptionData):
    logger.info(f"Activating trial subscription for tg_id: {data.tg_id}")
    try:
        trial_status = await get_trial_status(str(data.tg_id), pool)
        if trial_status is not None:
            raise HTTPException(status_code=400, detail="Вы уже активировали пробную подписку")
        email = f"DE-FRA-TRIAL-{data.tg_id}-{uuid.uuid4().hex[:6]}"
        current_panel = get_best_panel()
        if not current_panel:
            raise HTTPException(status_code=500, detail="No available panels")
        subscription_id = generate_sub(16)
        expiry_time = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
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
        api = get_api_by_name(current_panel['name'])
        api.client.add(1, [new_client])
        await add_subscription_to_db(str(data.tg_id), email, current_panel['name'], expiry_time, pool)
        await create_trial_user(str(data.tg_id), pool)
        subscription_key = current_panel["create_key"](new_client)
        logger.info(f"Trial subscription created for tg_id: {data.tg_id}, email: {email}")
        return {
            "email": email,
            "panel": current_panel['name'],
            "key": subscription_key,
            "expiry_date": expiry_time,
            "days": 3
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error creating trial subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating trial subscription: {str(e)}")
