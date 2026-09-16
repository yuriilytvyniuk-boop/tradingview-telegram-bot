import os
import datetime
import logging
import asyncpg

from fastapi import FastAPI, Request, HTTPException
from telegram import Bot


# ============================================================
# ЛОГУВАННЯ
# ============================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI()


# ============================================================
# DATABASE
# ============================================================

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "")

DATABASE_URL = RAW_DATABASE_URL.replace(
    "postgres://",
    "postgresql://",
    1
)

# Стара база даних на Render для міграції
RAW_OLD_DATABASE_URL = os.getenv("OLD_DATABASE_URL", "")
OLD_DATABASE_URL = RAW_OLD_DATABASE_URL.replace(
    "postgres://",
    "postgresql://",
    1
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

RAW_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "-1003940810691"
)

try:
    TELEGRAM_CHAT_ID = int(RAW_CHAT_ID)
except ValueError:
    logger.warning(
        f"Не вдалося розпарсити TELEGRAM_CHAT_ID "
        f"'{RAW_CHAT_ID}', використовуємо дефолтний ID."
    )
    TELEGRAM_CHAT_ID = -1003940810691


bot = (
    Bot(token=TELEGRAM_BOT_TOKEN)
    if TELEGRAM_BOT_TOKEN
    else None
)


# ============================================================
# НОРМАЛІЗАЦІЯ TICKER
# ============================================================

def normalize_ticker(ticker: str) -> str:
    if not ticker:
        return "UNKNOWN"
    ticker = str(ticker).strip().upper()
    if ticker.endswith(".P"):
        ticker = ticker[:-2]
    return ticker


# ============================================================
# DATABASE CONNECTION
# ============================================================

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)


# ============================================================
# МІГРАЦІЯ ДАНИХ ЗІ СТАРОЇ БАЗИ (ТИМЧАСОВА ФУНКЦІЯ)
# ============================================================

async def migrate_old_database():
    if not OLD_DATABASE_URL or not DATABASE_URL:
        logger.info("Міграція пропущена: OLD_DATABASE_URL або DATABASE_URL не вказані.")
        return

    logger.info("🚀 РОЗПОЧИНАЄМО МІГРАЦІЮ ДАНИХ ЗІ СТАРОЇ БАЗИ В SUPABASE...")

    try:
        conn_old = await asyncpg.connect(OLD_DATABASE_URL)
        conn_new = await asyncpg.connect(DATABASE_URL)

        # 1. Міграція active_trades
        active_rows = await conn_old.fetch("SELECT symbol, entry_price, direction, created_at FROM active_trades")
        for row in active_rows:
            await conn_new.execute(
                '''
                INSERT INTO active_trades (symbol, entry_price, direction, created_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (symbol) DO NOTHING;
                ''',
                row['symbol'], row['entry_price'], row['direction'], row['created_at']
            )
        logger.info(f"✅ Перенесено {len(active_rows)} записів з active_trades.")

        # 2. Міграція trade_history
        history_rows = await conn_old.fetch("SELECT symbol, direction, roi, closed_at FROM trade_history")
        for row in history_rows:
            await conn_new.execute(
                '''
                INSERT INTO trade_history (symbol, direction, roi, closed_at)
                VALUES ($1, $2, $3, $4);
                ''',
                row['symbol'], row['direction'], row['roi'], row['closed_at']
            )
        logger.info(f"✅ Перенесено {len(history_rows)} записів з trade_history.")

        # 3. Міграція monthly_roi
        monthly_rows = await conn_old.fetch("SELECT month_str, symbol, total_roi FROM monthly_roi")
        for row in monthly_rows:
            await conn_new.execute(
                '''
                INSERT INTO monthly_roi (month_str, symbol, total_roi)
                VALUES ($1, $2, $3)
                ON CONFLICT (month_str, symbol) DO NOTHING;
                ''',
                row['month_str'], row['symbol'], row['total_roi']
            )
        logger.info(f"✅ Перенесено {len(monthly_rows)} записів з monthly_roi.")

        await conn_old.close()
        await conn_new.close()
        logger.info("🎉 МІГРАЦІЯ УСПІШНО ЗАВЕРШЕНА!")

    except Exception as e:
        logger.error(f"❌ Помилка під час міграції: {e}", exc_info=True)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

@app.on_event("startup")
async def startup():
    if not DATABASE_URL:
        logger.warning(
            "DATABASE_URL не вказано! "
            "Функції збереження угод працювати не будуть."
        )
        return

    try:
        conn = await get_db_connection()

        # Таблиця відкритих угод
        await conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS active_trades (
                symbol TEXT PRIMARY KEY,
                entry_price DOUBLE PRECISION,
                direction TEXT,
                created_at TEXT
            );
            '''
        )

        # Таблиця історії угод (для підрахунку ROI)
        await conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS trade_history (
                id SERIAL PRIMARY KEY,
                symbol TEXT,
                direction TEXT,
                roi DOUBLE PRECISION,
                closed_at TIMESTAMP
            );
            '''
        )

        # Таблиця звітів за місяць
        await conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS monthly_roi (
                id SERIAL PRIMARY KEY,
                month_str TEXT,
                symbol TEXT,
                total_roi DOUBLE PRECISION,
                UNIQUE(month_str, symbol)
            );
            '''
        )

        await conn.close()
        logger.info("База даних успішно ініціалізована (додано історію та місячний ROI).")

        # АВТОМАТИЧНИЙ ЗАПУСК МІГРАЦІЇ ПРИ СТАРТІ
        await migrate_old_database()

    except Exception as e:
        logger.error(f"Помилка ініціалізації БД: {e}")


# ============================================================
# CRUD ФУНКЦІЇ ДЛЯ БД
# ============================================================

async def save_active_trade(symbol: str, entry_price: float, direction: str, created_at: str):
    if not DATABASE_URL: return
    conn = await get_db_connection()
    try:
        await conn.execute(
            '''
            INSERT INTO active_trades (symbol, entry_price, direction, created_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (symbol) DO UPDATE SET
                entry_price = EXCLUDED.entry_price,
                direction = EXCLUDED.direction,
                created_at = EXCLUDED.created_at;
            ''',
            symbol, entry_price, direction, created_at
        )
    finally:
        await conn.close()

async def get_active_trade(symbol: str):
    if not DATABASE_URL: return None
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow('SELECT entry_price, direction FROM active_trades WHERE symbol = $1', symbol)
        if row:
            return {"entry_price": row["entry_price"], "direction": row["direction"]}
        return None
    finally:
        await conn.close()

async def delete_active_trade(symbol: str):
    if not DATABASE_URL: return
    conn = await get_db_connection()
    try:
        await conn.execute('DELETE FROM active_trades WHERE symbol = $1', symbol)
    finally:
        await conn.close()

async def save_trade_history(symbol: str, direction: str, roi: float):
    if not DATABASE_URL: return
    conn = await get_db_connection()
    try:
        now_ts = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        await conn.execute(
            '''
            INSERT INTO trade_history (symbol, direction, roi, closed_at)
            VALUES ($1, $2, $3, $4)
            ''',
            symbol, direction, roi, now_ts
        )
    finally:
        await conn.close()


# ============================================================
# МІСЯЧНИЙ ЗВІТ (НОВИЙ ФУНКЦІОНАЛ)
# ============================================================

@app.get("/calculate-monthly-roi")
async def calculate_monthly_roi():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL is not set")

    conn = await get_db_connection()
    
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        first_day_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_prev_month = first_day_this_month - datetime.timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        month_str = first_day_prev_month.strftime("%Y-%m")
        
        records = await conn.fetch(
            '''
            SELECT symbol, SUM(roi) as coin_roi
            FROM trade_history
            WHERE closed_at >= $1 AND closed_at < $2
            GROUP BY symbol
            ''',
            first_day_prev_month.replace(tzinfo=None),
            first_day_this_month.replace(tzinfo=None)
        )
        
        if not records:
            return {"status": "ok", "message": f"Немає угод за {month_str}"}

        total_all_coins_roi = 0.0
        report_lines = [f"📊 <b>Звіт за місяць: {month_str}</b>\n"]
        
        for rec in records:
            symbol = rec["symbol"]
            coin_roi = rec["coin_roi"]
            total_all_coins_roi += coin_roi
            
            await conn.execute(
                '''
                INSERT INTO monthly_roi (month_str, symbol, total_roi)
                VALUES ($1, $2, $3)
                ON CONFLICT (month_str, symbol) DO UPDATE SET
                    total_roi = EXCLUDED.total_roi;
                ''',
                month_str, symbol, coin_roi
            )
            
            roi_sym = "📈" if coin_roi >= 0 else "📉"
            report_lines.append(f"🪙 #{symbol}: {roi_sym} {coin_roi:+.2f}%")
        
        await conn.execute(
            '''
            INSERT INTO monthly_roi (month_str, symbol, total_roi)
            VALUES ($1, $2, $3)
            ON CONFLICT (month_str, symbol) DO UPDATE SET
                total_roi = EXCLUDED.total_roi;
            ''',
            month_str, "ALL_COINS", total_all_coins_roi
        )

        total_sym = "🚀" if total_all_coins_roi >= 0 else "🩸"
        report_lines.append(f"\n{total_sym} <b>Загальний ROI: {total_all_coins_roi:+.2f}%</b>")
        
        report_message = "\n".join(report_lines)
        
        if bot:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=report_message,
                parse_mode="HTML"
            )

        return {"status": "ok", "month": month_str, "total_roi": total_all_coins_roi}

    except Exception as e:
        logger.error(f"Помилка підрахунку місячного ROI: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await conn.close()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Kerdos Bot Webhook Service is running"
    }


# ============================================================
# WEBHOOK
# ============================================================

@app.post("/webhook")
async def webhook(request: Request):

    if not bot:
        logger.error("Запит до /webhook, але бот не ініціалізований.")
        raise HTTPException(status_code=500, detail="Bot token missing")

    try:
        data = await request.json()
        logger.info(f"Отримано сигнал: {data}")
    except Exception as e:
        logger.error(f"Помилка розпарсингу JSON: {e}")
        return {"status": "error", "message": "Invalid JSON"}

    raw_ticker = data.get("ticker", "UNKNOWN")
    ticker = normalize_ticker(raw_ticker)
    
    raw_action = str(data.get("action", "")).lower()
    market_pos = str(data.get("strategy", "")).lower()

    try:
        price = float(data.get("price", 0))
    except (ValueError, TypeError):
        price = 0.0

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    is_exit = (
        market_pos == "flat"
        or "exit" in raw_action
        or "close" in raw_action
        or "tp" in raw_action
        or "sl" in raw_action
    )

    if is_exit:
        trade_info = await get_active_trade(ticker)

        if trade_info:
            entry_price = trade_info["entry_price"]
            direction_type = trade_info["direction"]

            if direction_type == "long":
                roi = (((price - entry_price) / entry_price) * 100) if entry_price else 0.0
                action_label = "🔒 CLOSE LONG POSITION"
            else:
                roi = (((entry_price - price) / entry_price) * 100) if entry_price else 0.0
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

            await save_trade_history(ticker, direction_type, roi)
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

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        logger.error(f"Помилка Telegram: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

    return {"status": "ok", "ticker": ticker}

