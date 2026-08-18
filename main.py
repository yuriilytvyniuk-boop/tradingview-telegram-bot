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
    """
    Очищає ticker від суфікса .P

    Приклади:

    BTCUSDT.P  -> BTCUSDT
    ETHUSDT.P  -> ETHUSDT
    SOLUSDT.P  -> SOLUSDT
    BTCUSDT    -> BTCUSDT
    """

    if not ticker:
        return "UNKNOWN"

    ticker = str(ticker).strip().upper()

    # Видаляємо .P тільки в кінці тикера
    if ticker.endswith(".P"):
        ticker = ticker[:-2]

    return ticker


# ============================================================
# DATABASE CONNECTION
# ============================================================

async def get_db_connection():
    return await asyncpg.connect(DATABASE_URL)


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

        await conn.close()

        logger.info(
            "Таблиця 'active_trades' успішно ініціалізована в БД."
        )

    except Exception as e:

        logger.error(
            f"Помилка ініціалізації БД: {e}"
        )


# ============================================================
# SAVE ACTIVE TRADE
# ============================================================

async def save_active_trade(
    symbol: str,
    entry_price: float,
    direction: str,
    created_at: str
):

    if not DATABASE_URL:
        return

    conn = await get_db_connection()

    try:

        await conn.execute(
            '''
            INSERT INTO active_trades
            (
                symbol,
                entry_price,
                direction,
                created_at
            )
            VALUES ($1, $2, $3, $4)

            ON CONFLICT (symbol)
            DO UPDATE SET
                entry_price = EXCLUDED.entry_price,
                direction = EXCLUDED.direction,
                created_at = EXCLUDED.created_at;
            ''',
            symbol,
            entry_price,
            direction,
            created_at
        )

    finally:

        await conn.close()


# ============================================================
# GET ACTIVE TRADE
# ============================================================

async def get_active_trade(symbol: str):

    if not DATABASE_URL:
        return None

    conn = await get_db_connection()

    try:

        row = await conn.fetchrow(
            '''
            SELECT
                entry_price,
                direction
            FROM active_trades
            WHERE symbol = $1
            ''',
            symbol
        )

        if row:

            return {
                "entry_price": row["entry_price"],
                "direction": row["direction"]
            }

        return None

    finally:

        await conn.close()


# ============================================================
# DELETE ACTIVE TRADE
# ============================================================

async def delete_active_trade(symbol: str):

    if not DATABASE_URL:
        return

    conn = await get_db_connection()

    try:

        await conn.execute(
            '''
            DELETE FROM active_trades
            WHERE symbol = $1
            ''',
            symbol
        )

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

    # --------------------------------------------------------
    # Перевірка Telegram бота
    # --------------------------------------------------------

    if not bot:

        logger.error(
            "Запит до /webhook, але бот не ініціалізований "
            "(відсутній TELEGRAM_BOT_TOKEN)."
        )

        raise HTTPException(
            status_code=500,
            detail="Bot token missing"
        )


    # --------------------------------------------------------
    # Отримання JSON
    # --------------------------------------------------------

    try:

        data = await request.json()

        logger.info(
            f"Отримано сигнал від TradingView: {data}"
        )

    except Exception as e:

        logger.error(
            f"Помилка розпарсингу JSON: {e}"
        )

        return {
            "status": "error",
            "message": "Invalid JSON"
        }


    # ========================================================
    # TICKER
    # ========================================================

    raw_ticker = data.get(
        "ticker",
        "UNKNOWN"
    )

    # Наприклад:
    # BTCUSDT.P -> BTCUSDT

    ticker = normalize_ticker(raw_ticker)

    logger.info(
        f"Ticker: {raw_ticker} -> {ticker}"
    )


    # ========================================================
    # ACTION
    # ========================================================

    raw_action = str(
        data.get("action", "")
    ).lower()


    # ========================================================
    # STRATEGY / POSITION
    # ========================================================

    market_pos = str(
        data.get("strategy", "")
    ).lower()


    # ========================================================
    # PRICE
    # ========================================================

    try:

        price = float(
            data.get("price", 0)
        )

    except (ValueError, TypeError):

        price = 0.0


    # ========================================================
    # TIME
    # ========================================================

    now_str = (
        datetime.datetime.now(
            datetime.timezone.utc
        )
        .strftime("%Y-%m-%d %H:%M UTC")
    )


    # ========================================================
    # EXIT CHECK
    # ========================================================

    is_exit = (
        market_pos == "flat"
        or "exit" in raw_action
        or "close" in raw_action
        or "tp" in raw_action
        or "sl" in raw_action
    )


    # ========================================================
    # CLOSE POSITION
    # ========================================================

    if is_exit:

        trade_info = await get_active_trade(
            ticker
        )


        # ----------------------------------------------------
        # Якщо угода знайдена
        # ----------------------------------------------------

        if trade_info:

            entry_price = trade_info[
                "entry_price"
            ]

            direction_type = trade_info[
                "direction"
            ]


            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if direction_type == "long":

                roi = (
                    (
                        (price - entry_price)
                        / entry_price
                    ) * 100
                    if entry_price
                    else 0.0
                )

                action_label = (
                    "🔒 CLOSE LONG POSITION"
                )


            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                roi = (
                    (
                        (entry_price - price)
                        / entry_price
                    ) * 100
                    if entry_price
                    else 0.0
                )

                action_label = (
                    "🔒 CLOSE SHORT POSITION"
                )


            # ------------------------------------------------
            # ROI ICON
            # ------------------------------------------------

            roi_symbol = (
                "📈"
                if roi >= 0
                else "📉"
            )


            # ------------------------------------------------
            # MESSAGE
            # ------------------------------------------------

            message = (
                f"⚡️ KERDOS SIGNAL ⚡️\n\n"
                f"🪙 Coin: #{ticker}\n"
                f"🎯 Action: {action_label}\n"
                f"💵 Entry Price: {entry_price}\n"
                f"💵 Close Price: {price}\n"
                f"{roi_symbol} ROI: {roi:+.2f}%\n"
                f"⏰ Time: {now_str}"
            )


            # Видаляємо угоду з БД

            await delete_active_trade(
                ticker
            )


        # ----------------------------------------------------
        # Якщо угоду НЕ знайдено
        # ----------------------------------------------------

        else:

            message = (
                f"⚡️ KERDOS SIGNAL ⚡️\n\n"
                f"🪙 Coin: #{ticker}\n"
                f"🎯 Action: 🔒 CLOSE POSITION\n"
                f"💵 Close Price: {price}\n"
                f"⚠️ Entry Price: Not found in DB\n"
                f"⏰ Time: {now_str}"
            )


    # ========================================================
    # OPEN POSITION
    # ========================================================

    else:

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        if (
            "short" in market_pos
            or "sell" in raw_action
        ):

            direction_type = "short"

            action_label = (
                "🔴 SELL / SHORT"
            )


        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        else:

            direction_type = "long"

            action_label = (
                "🟢 BUY / LONG"
            )


        # ----------------------------------------------------
        # SAVE TO DATABASE
        # ----------------------------------------------------

        await save_active_trade(
            ticker,
            price,
            direction_type,
            now_str
        )


        # ----------------------------------------------------
        # TELEGRAM MESSAGE
        # ----------------------------------------------------

        message = (
            f"⚡️ KERDOS SIGNAL ⚡️\n\n"
            f"🪙 Coin: #{ticker}\n"
            f"🎯 Action: {action_label}\n"
            f"💵 Entry Price: {price}\n"
            f"⏰ Time: {now_str}"
        )


    # ========================================================
    # SEND TELEGRAM MESSAGE
    # ========================================================

    try:

        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message
        )

        logger.info(
            f"Сигнал для #{ticker} успішно "
            f"надіслано в чат {TELEGRAM_CHAT_ID}!"
        )


    except Exception as e:

        logger.error(
            f"Помилка надсилання повідомлення "
            f"в Telegram: {e}",
            exc_info=True
        )

        return {
            "status": "error",
            "message": str(e)
        }


    # ========================================================
    # RESPONSE
    # ========================================================

    return {
        "status": "ok",
        "ticker": ticker
    }
