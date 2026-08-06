import os
import requests
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

# 🔐 Вставте ваш токен з BotFather замість ВАШ_ТОКЕН_З_BOTFATHER
BOT_TOKEN = "ВАШ_ТОКЕН_З_BOTFATHER"
CHAT_ID = "-1004369884950" 

@app.get("/")
def read_root():
    return {"status": "Bot is running!"}

@app.post("/webhook")
async def receive_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format")

    ticker = data.get("ticker", "N/A")
    action = data.get("action", "SIGNAL").upper()
    price = data.get("price", "N/A")
    time_str = data.get("time", "")
    comment = data.get("comment", "")

    emoji = "🟢" if "BUY" in action or "LONG" in action else "🔴"
    
    text = (
        f"{emoji} **TRADINGVIEW СИГНАЛ** {emoji}\n\n"
        f"📊 **Пара:** `{ticker}`\n"
        f"🎯 **Дія:** `{action}`\n"
        f"💰 **Ціна:** `{price}`\n"
    )
    
    if comment:
        text += f"📝 **Примітка:** {comment}\n"
    if time_str:
        text += f"⏰ **Час:** {time_str}"

    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    requests.post(telegram_url, json=payload)
    return {"status": "success"}
