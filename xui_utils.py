from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
import aiosqlite
from database import get_user, create_user, update_user_terms
from xui_utils import get_active_subscriptions
from xui_utils import get_best_panel, get_api_by_name
import config as cfg
import hmac
import hashlib
import urllib.parse
import logging

app = FastAPI()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuthData(BaseModel):
    init_data: str


def verify_init_data(init_data: str) -> dict:
    """
    Проверяет подлинность initData от Telegram Web Apps.
    Возвращает словарь с данными пользователя или вызывает исключение.
    """
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        received_hash = parsed_data.pop('hash', None)
        data_check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed_data.items()))
        secret_key = hashlib.sha256(cfg.API_TOKEN.encode()).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if computed_hash != received_hash:
            raise HTTPException(status_code=401, detail="Недействительные данные авторизации")

        user_data = urllib.parse.parse_qs(init_data).get('user', [''])[0]
        return {'user': eval(user_data)}  # Преобразуем JSON-строку в словарь
    except Exception as e:
        logger.error(f"Ошибка проверки initData: {e}")
        raise HTTPException(status_code=400, detail=f"Ошибка обработки initData: {str(e)}")


@app.post("/api/auth")
async def auth(data: AuthData):
    logger.info(f"Received init_data: {data.init_data}")
    user_data = verify_init_data(data.init_data)
    tg_id = user_data['user']['id']
    user = await get_user(tg_id)

    if not user:
        await create_user(tg_id)
        user = await get_user(tg_id)

    if not user['accepted_terms']:
        await update_user_terms(tg_id, True)  # Автоматическое принятие условий для прототипа
        user['accepted_terms'] = True

    logger.info(f"Authenticated user: {tg_id}")
    return {"user": {"telegram_id": tg_id, "first_name": user_data['user'].get('first_name', '')}}


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
