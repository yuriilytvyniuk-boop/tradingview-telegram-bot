import os
import json
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import telegram
import aiosqlite

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
DB_PATH = "trades.db"

app = FastAPI()
bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                action TEXT,
                price REAL,
                roi REAL,
                timestamp DATETIME
            )
        """)
        await db.commit()

@app.on_event("startup")
async def startup_event():
    await init_db()

@app.post("/webhook")
async def webhook(request: Request):
    try:
        raw_body = await request.body()
        data = json.loads(raw_body.decode("utf-8"))
        
        ticker = data.get("ticker", "UNKNOWN").upper()
        action = data.get("action", "").lower()
        price = float(data.get("price", 0.0))
        entry_price_raw = data.get("entry_price", "0")
        
        try:
            entry_price = float(entry_price_raw)
        except (ValueError, TypeError):
            entry_price = 0.0

        now = datetime.now(timezone.utc)
        message_text = ""

        if action in ["buy", "long"]:
            message_text = (
                f"🚀 **ENTRY LONG**\n\n"
                f"🪙 **Asset:** #{ticker}\n"
                f"💵 **Entry Price:** {price:.4f}\n"
                f"⏰ **Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
        elif action in ["sell", "short"]:
            message_text = (
                f"🔻 **ENTRY SHORT**\n\n"
                f"🪙 **Asset:** #{ticker}\n"
                f"💵 **Entry Price:** {price:.4f}\n"
                f"⏰ **Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
        elif action in ["close", "exit"] or entry_price != 0:
            roi = 0.0
            if entry_price > 0:
                roi = ((price - entry_price) / entry_price) * 100
                
            roi_emoji = "🟢" if roi >= 0 else "🔴"
            
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT INTO trades (ticker, action, price, roi, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (ticker, action, price, roi, now.isoformat())
                )
                await db.commit()

            message_text = (
                f"🏁 **POSITION CLOSED**\n\n"
                f"🪙 **Asset:** #{ticker}\n"
                f"💵 **Exit Price:** {price:.4f}\n"
                f"📈 **Result (ROI):** {roi_emoji} **{roi:+.2f}%**\n"
                f"⏰ **Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )

        if message_text and bot and TELEGRAM_CHANNEL_ID:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=message_text,
                parse_mode="Markdown"
            )

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/monthly_report")
async def generate_monthly_report():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT roi FROM trades") as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        report_text = "📊 **MONTHLY PERFORMANCE REPORT**\n\nNo trades closed this month."
    else:
        rois = [r[0] for r in rows]
        total_trades = len(rois)
        winning_trades = sum(1 for r in rois if r > 0)
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        total_roi = sum(rois)
        
        roi_emoji = "🟢" if total_roi >= 0 else "🔴"
        
        report_text = (
            f"📊 **MONTHLY PERFORMANCE REPORT**\n\n"
            f"🔢 **Total Trades:** {total_trades}\n"
            f"🎯 **Win Rate:** {win_rate:.1f}%\n"
            f"💰 **Total Net ROI:** {roi_emoji} **{total_roi:+.2f}%**\n\n"
            f"💡 *All signals generated automatically by Mireya system.*"
        )
        
    if bot and TELEGRAM_CHANNEL_ID:
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID,
            text=report_text,
            parse_mode="Markdown"
        )
        
    return {"status": "ok", "report": report_text}
