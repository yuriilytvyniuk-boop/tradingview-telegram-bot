import os
import datetime
import asyncpg
from fastapi import FastAPI, Request
from telegram import Bot

app = FastAPI()

# Перетворення URL для asyncpg (Render видає postgres://, а asyncpg чекає postgresql://)
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

bot = Bot(token=TELEGRAM_BOT_TOKEN)

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)

@app.on_event("startup")
async def startup():
    if DATABASE_URL:
        conn = await get_db_connection()
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS active_trades (
                symbol TEXT PRIMARY KEY,
                entry_price DOUBLE PRECISION,
                direction TEXT,
                created_at TEXT
            );
        ''')
        await conn.close()

async def save_active_trade(symbol: str, entry_price: float, direction: str, created_at: str):
    conn = await get_db_connection()
    await conn.execute('''
        INSERT INTO active_trades (symbol, entry_price, direction, created_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (symbol) DO UPDATE 
        SET entry_price = EXCLUDED.entry_price,
            direction = EXCLUDED.direction,
            created_at = EXCLUDED.created_at;
    ''', symbol, entry_price, direction, created_at)
    await conn.close()

async def get_active_trade(symbol: str):
    conn = await get_db_connection()
    row = await conn.fetchrow('SELECT entry_price, direction FROM active_trades WHERE symbol = $1', symbol)
    await conn.close()
    if row:
        return {"entry_price": row["entry_price"], "direction": row["direction"]}
    return None

async def delete_active_trade(symbol: str):
    conn = await get_db_connection()
    await conn.execute('DELETE FROM active_trades WHERE symbol = $1', symbol)
    await conn.close()

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    ticker = data.get("ticker", "UNKNOWN").upper()
    raw_action = str(data.get("action", "")).lower()
    market_pos = str(data.get("strategy", "")).lower()
    
    try:
        price = float(data.get("price", 0))
    except (ValueError, TypeError):
        price = 0.0

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Перевірка на закриття угоди
    is_exit = (
        market_pos == "flat" or 
        "exit" in raw_action or 
        "close" in raw_action or 
        "tp" in raw_action or 
        "sl" in raw_action
    )

    if is_exit:
        trade_info = await get_active_trade(ticker)

        if trade_info:
            entry_price = trade_info["entry_price"]
            direction_type = trade_info["direction"]

            if direction_type == "long":
                roi = ((price - entry_price) / entry_price) * 100
                action_label = "🔒 CLOSE LONG POSITION"
            else:
                roi = ((entry_price - price) / entry_price) * 100
                action_label = "🔒 CLOSE SHORT POSITION"

            roi_symbol = "📈" if roi >= 0 else "📉"
            
            message = (
                f"⚡️ KERDOS SIGNAL ⚡️\n\n"
                f"🪙 Coin: #{ticker}\n"
                f"🎯 Action: {action_label}\n"
                f"💵 Entry Price: {entry_price}\n"
                f"💵 Close Price: {price}\n"
                f"{roi_symbol} ROI: {roi:+.2f}%\n"
                f"⏰ Time: {now_str}"
            )
            await delete_active_trade(ticker)
        else:
            message = (
                f"⚡️ KERDOS SIGNAL ⚡️\n\n"
                f"🪙 Coin: #{ticker}\n"
                f"🎯 Action: 🔒 CLOSE POSITION\n"
                f"💵 Close Price: {price}\n"
                f"⚠️ Entry Price: Not found in DB\n"
                f"⏰ Time: {now_str}"
            )
    else:
        # Відкриття угоди (LONG / SHORT)
        if "short" in market_pos or "sell" in raw_action:
            direction_type = "short"
            action_label = "🔴 SELL / SHORT"
        else:
            direction_type = "long"
            action_label = "🟢 BUY / LONG"

        await save_active_trade(ticker, price, direction_type, now_str)

        message = (
            f"⚡️ KERDOS SIGNAL ⚡️\n\n"
            f"🪙 Coin: #{ticker}\n"
            f"🎯 Action: {action_label}\n"
            f"💵 Entry Price: {price}\n"
            f"⏰ Time: {now_str}"
        )

    # Надсилання сигналу в Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

    return {"status": "ok"}
