import sqlite3
from datetime import date
from fastapi import FastAPI, Request
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler

app = FastAPI()

# ----------------- НАЛАШТУВАННЯ TELEGRAM -----------------
TELEGRAM_BOT_TOKEN = "ВАШ_ТОКЕН_БОТА"
TELEGRAM_CHAT_ID = "ВАШ_CHAT_ID"

# ----------------- БАЗА ДАНИХ -----------------
def init_db():
    conn = sqlite3.connect("trading_stats.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            roi_pct REAL,
            date_closed TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_trade(ticker: str, roi_pct: float):
    conn = sqlite3.connect("trading_stats.db")
    cursor = conn.cursor()
    today_str = date.today().isoformat()
    cursor.execute("INSERT INTO trades (ticker, roi_pct, date_closed) VALUES (?, ?, ?)",
                   (ticker, roi_pct, today_str))
    conn.commit()
    conn.close()

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending telegram message: {e}")

# ----------------- ЩОМІСЯЧНИЙ ЗВІТ (1-го числа) -----------------
def generate_monthly_report():
    today = date.today()
    if today.day != 1:
        return

    conn = sqlite3.connect("trading_stats.db")
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, roi_pct FROM trades")
    rows = cursor.fetchall()

    if not rows:
        message = "📅 <b>ЩОМІСЯЧНИЙ ЗВІТ</b>\n\nЗа минулий місяць закритих угод не зафіксовано."
        send_telegram(message)
        conn.close()
        return

    total_roi = sum(r[1] for r in rows)
    total_trades = len(rows)
    wins = sum(1 for r in rows if r[1] > 0)
    losses = sum(1 for r in rows if r[1] < 0)
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0

    roi_icon = "🟢" if total_roi >= 0 else "🔴"

    message = (
        f"📅 <b>ЗВІТ ЗА МИНУЛИЙ МІСЯЦЬ</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📈 <b>Загальний ROI:</b> {roi_icon} <b>{total_roi:+.2f}%</b>\n\n"
        f"🔢 <b>Усього угод:</b> {total_trades}\n"
        f"✅ <b>Прибуткових (Win):</b> {wins}\n"
        f"❌ <b>Збиткових (Loss):</b> {losses}\n"
        f"🎯 <b>Вінрейт (Winrate):</b> {winrate:.1f}%\n"
    )

    send_telegram(message)

    # Очищаємо підсумок у базі для нового місяця
    cursor.execute("DELETE FROM trades")
    conn.commit()
    conn.close()

# ----------------- ПЛАНУВАЛЬНИК (щодня о 09:00) -----------------
scheduler = AsyncIOScheduler()
scheduler.add_job(generate_monthly_report, 'cron', hour=9, minute=0)

@app.on_event("startup")
def startup_event():
    scheduler.start()

# ----------------- ВЕБХУК TRADINGVIEW -----------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    action = str(data.get("action", "")).lower()
    ticker = data.get("ticker", "N/A")
    price = float(data.get("price", 0))
    entry_price = float(data.get("entry_price", 0)) if data.get("entry_price") else None

    # Вхід у LONG
    if "buy" in action or "long" in action:
        message = (
            f"🚀 <b>ВХІД У LONG (ПОКУПКА)</b>\n\n"
            f"🪙 <b>Монета:</b> #{ticker}\n"
            f"💵 <b>Ціна входу:</b> {price}\n"
        )
    # Вхід у SHORT
    elif "sell" in action or "short" in action:
        message = (
            f"🔻 <b>ВХІД У SHORT (ПРОДАЖ)</b>\n\n"
            f"🪙 <b>Монета:</b> #{ticker}\n"
            f"💵 <b>Ціна входу:</b> {price}\n"
        )
    # Закриття позиції (Exit / Close / TP / SL)
    elif "close" in action or "exit" in action or "tp" in action or "sl" in action:
        roi_text = ""
        if entry_price and entry_price > 0:
            if "long" in action or "buy" in action:
                roi_pct = ((price - entry_price) / entry_price) * 100
            else:
                roi_pct = ((entry_price - price) / entry_price) * 100

            save_trade(ticker, roi_pct)

            roi_icon = "🟢" if roi_pct >= 0 else "🔴"
            roi_text = f"📈 <b>Результат (ROI):</b> {roi_icon} {roi_pct:+.2f}%\n"

        message = (
            f"🏁 <b>ЗАКРИТТЯ ПОЗИЦІЇ</b>\n\n"
            f"🪙 <b>Монета:</b> #{ticker}\n"
            f"💵 <b>Ціна виходу:</b> {price}\n"
            f"{roi_text}"
        )
    # Інші довільні сигнали
    else:
        message = (
            f"🔔 <b>СИГНАЛ TRADINGVIEW</b>\n\n"
            f"🪙 <b>Монета:</b> #{ticker}\n"
            f"📍 <b>Подія:</b> {action.upper()}\n"
            f"💵 <b>Ціна:</b> {price}\n"
        )

    send_telegram(message)
    return {"status": "ok"}
