import aiosqlite
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
import uuid
from py3xui import Client
from database import get_trial_status, create_trial_user, activate_trial, add_subscription_to_db, get_user, create_user, \
    update_user_terms
from xui_utils import get_best_panel, get_api_by_name, PANELS
import config as cfg
import hmac
import hashlib
import urllib.parse

app = FastAPI()


class AuthData(BaseModel):
    init_data: str


def verify_init_data(init_data: str) -> dict:
    """
    Проверяет подлинность initData от Telegram Web Apps.
    Возвращает словарь с данными пользователя или вызывает исключение.
    """
    parsed_data = dict(urllib.parse.parse_qsl(init_data))
    received_hash = parsed_data.pop('hash', None)
    data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed_data.items()))
    secret_key = hashlib.sha256(cfg.API_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if computed_hash != received_hash:
        raise HTTPException(status_code=401, detail="Недействительные данные авторизации")

    return parsed_data


@app.post("/api/auth")
async def auth(data: AuthData):
    user_data = verify_init_data(data.init_data)
    tg_id = int(user_data.get('user').split(':')[1])  # Извлекаем tg_id из user JSON
    user = await get_user(tg_id)

    if not user:
        await create_user(tg_id)
        user = await get_user(tg_id)

    if not user['accepted_terms']:
        await update_user_terms(tg_id, True)  # Автоматическое принятие условий для упрощения прототипа

    return {"user": user}


@app.get("/api/trial/status")
async def get_trial_status_endpoint(tg_id: int):
    status = await get_trial_status(tg_id)
    key = None
    if status == 1:
        async with aiosqlite.connect("subscriptions.db", timeout=10.0) as conn:
            cursor = await conn.cursor()
            await cursor.execute("SELECT email FROM users WHERE tg_id = ? AND email LIKE 'DE-FRA-TRIAL%'", (tg_id,))
            result = await cursor.fetchone()
            if result:
                email = result[0]
                for panel in PANELS:
                    inbounds = panel["api"].inbound.get_list()
                    for inbound in inbounds:
                        for client in inbound.settings.clients:
                            if client.email == email:
                                key = panel["create_key"](client)
                                break
                    if key:
                        break
    return {"status": status, "key": key}


@app.post("/api/trial/activate")
async def activate_trial_endpoint(tg_id: int):
    trial_status = await get_trial_status(tg_id)
    if trial_status is None:
        await create_trial_user(tg_id)
        trial_status = 0

    if trial_status == 1:
        raise HTTPException(status_code=400, detail="Пробный период уже активирован")

    expiry_time = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    email = f"DE-FRA-TRIAL-{tg_id}"
    subscription_id = str(uuid.uuid4())[:16]
    trial_client = Client(
        id=str(uuid.uuid4()),
        enable=True,
        tg_id=tg_id,
        expiry_time=int(datetime.strptime(expiry_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000),
        flow="xtls-rprx-vision",
        email=email,
        sub_id=subscription_id,
        limit_ip=1
    )
    current_panel = get_best_panel()
    api = get_api_by_name(current_panel['name'])
    api.client.add(1, [trial_client])
    trial_key = current_panel["create_key"](trial_client)
    await add_subscription_to_db(tg_id, email, current_panel['name'], expiry_time)
    await activate_trial(tg_id)
    return {"key": trial_key}