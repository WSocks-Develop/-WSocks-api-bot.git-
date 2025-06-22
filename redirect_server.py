from fastapi import FastAPI, Request, Query
from fastapi.responses import RedirectResponse
import urllib.parse

app = FastAPI()

@app.get("/")
async def home():
    return {"message": "V2Ray Redirect Server is running!"}

@app.get("/")
def redirect_v2ray(key: str = Query(..., description="Encoded V2Ray Key")):
    decoded_key = urllib.parse.unquote(key)  # Декодируем ключ
    new_url = f"v2raytun://import/{decoded_key}"  # Формируем правильную ссылку
    return RedirectResponse(url=new_url)

# Запускаем сервер: uvicorn redirect_server:app --host 0.0.0.0 --port 8080