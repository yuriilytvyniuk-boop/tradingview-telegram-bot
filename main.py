import os
import datetime
import logging
import asyncpg
from fastapi import FastAPI, Request, HTTPException
from telegram import Bot

# Налаштування логування
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# База даних (адаптація URL для asyncpg)
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "")
DATABASE_URL = RAW_DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Telegram налаштування
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Гарантоване отримання TELEGRAM_CHAT_ID як int
RAW_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003940810691")
try:
    TELEGRAM_CHAT_ID = int(RAW_CHAT_ID)
except ValueError:
    logger.warning(f"Не вдалося розпарсити TELEGRAM_CHAT_ID '{RAW_CHAT_ID}', використовуємо дефолтний ID.")
    TELEGRAM_CHAT_ID = -1003940810691

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None


# Робота з PostgreSQL
async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)


@app.on_event("startup")
async def startup():
    if DATABASE_URL:
        try:
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
            logger.info("Таблиця 'active_trades' успішно ініціалізована в БД.")
        except Exception as e:
            logger.error(f"Помилка ініціалізації БД: {e}")
    else:
        logger.warning("DATABASE_URL не вказано! Функції збереження угод працювати не будуть.")


async def save_active_trade(symbol: str, entry_price: float, direction: str, created_at: str):
    if not DATABASE_URL:
        return
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
    if not DATABASE_URL:
        return None
    conn = await get_db_connection()
    row = await conn.fetchrow('SELECT entry_price, direction FROM active_trades WHERE symbol = $1', symbol)
    await conn.close()
    if row:
        return {"entry_price": row["entry_price"], "direction": row["direction"]}
    return None


async def delete_active_trade(symbol: str):
    if not DATABASE_URL:
        return
    conn = await get_db_connection()
    await conn.execute('DELETE FROM active_trades WHERE symbol = $1', symbol)
    await conn.close()


@app.get("/")
async def root():
    return {"status": "ok", "message": "Kerdos Bot Webhook Service is running"}


@app.post("/webhook")
async def webhook(request: Request):
    if not bot:
        logger.error("Запит до /webhook, але бот не ініціалізований (відсутній токен).")
        raise HTTPException(status_code=500, detail="Bot token missing")

    try:
        data = await request.json()
        logger.info(f"Отримано сигнал від TradingView: {data}")
    except Exception as e:
        logger.error(f"Помилка розпарсингу JSON: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    ticker = data.get("ticker", "UNKNOWN").upper()
    raw_action = str(data.get("action", "")).lower()
    market_pos = str(data.get("strategy", "")).lower()
    
    try:
        price = float(data.get("price", 0))
    except (ValueError, TypeError):
        price = 0.0

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Перевірка на закриття угоди (exit/close/tp/sl або flat)
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
                roi = ((price - entry_price) / entry_price) * 100 if entry_price else 0.0
                action_label = "🔒 CLOSE LONG POSITION"
            else:
                roi = ((entry_price - price) / entry_price) * 100 if entry_price else 0.0
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
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.info(f"Сигнал для #{ticker} успішно надіслано в чат {TELEGRAM_CHAT_ID}!")
    except Exception as e:
        logger.error(f"Помилка надсилання повідомлення в Telegram: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}
