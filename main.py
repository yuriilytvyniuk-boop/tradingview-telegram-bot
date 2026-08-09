import os
import json
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# --- ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ КНОПОК ТА МЕНЮ ---

def get_main_keyboard(lang="en"):
    """Генерує сітку Inline-кнопок"""
    if lang == "ua":
        keyboard = [
            [InlineKeyboardButton("🎁 Отримати 14 днів FREE", callback_data="btn_free_trial")],
            [InlineKeyboardButton("📊 Доступ до VIP-групи ($20/міс)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Підключити Signal Bot ($100/міс)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Послуги та ціни", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Правила спільноти", callback_data="btn_rules")],
            [InlineKeyboardButton("🇬🇧 Switch to English", callback_data="lang_en")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🎁 Get 14-Day Free Trial", callback_data="btn_free_trial")],
            [InlineKeyboardButton("📊 VIP Signals Group Access ($20/mo)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Connect Signal Bot ($100/mo)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Services & Pricing", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Community Rules", callback_data="btn_rules")],
            [InlineKeyboardButton("🇺🇦 Переключити на Українську", callback_data="lang_ua")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_text_start(lang="en"):
    if lang == "ua":
        return (
            "👋 **Вітаємо у спільноті!**\n\n"
            "Я — **Mireya**, твій персональний помічник із підключення та навігації. "
            "Обери потрібну опцію з меню нижче:"
        )
    return (
        "👋 **Welcome to the community!**\n\n"
        "I am **Mireya**, your personal setup and navigation assistant. "
        "Please choose an option from the menu below:"
    )

def get_text_services(lang="en"):
    if lang == "ua":
        return (
            "💎 **Наші Послуги та Прайс**\n\n"
            "📊 **VIP-група з сигналами:** **$20 / місяць**\n"
            "*(Аналітика, торгові сигнали та доступ до чату спільноти)*\n\n"
            "🤖 **Персональний Signal Bot:** **$100 / місяць**\n"
            "*(Пряме автоматичне підключення бота для миттєвого виконання сигналів)*\n\n"
            "🎁 **Бонуси:**\n"
            "• **14 днів FREE** для нових користувачів!\n"
            "• **+14 днів** за кожного приведеного друга, який придбає підписку!"
        )
    return (
        "💎 **Services & Pricing**\n\n"
        "📊 **VIP Signals Group Access:** **$20 / month**\n"
        "*(Market analytics, trade signals, and community access)*\n\n"
        "🤖 **Personal Signal Bot Setup:** **$100 / month**\n"
        "*(Direct automated bot connection for instant signal execution)*\n\n"
        "🎁 **Bonuses:**\n"
        "• **14-Day FREE Trial** for new users!\n"
        "• **+14 Days Free Access** for every referred friend who subscribes!"
    )

def get_text_rules(lang="en"):
    if lang == "ua":
        return (
            "📜 **Правила спільноти**\n\n"
            "🚫 **Без спаму та флуду:** Заборонено смітити в чаті.\n"
            "❌ **Заборона реклами:** Реклама та реферальні посилання заборонені.\n"
            "🤝 **Повага:** Образи та токсичність неприпустимі.\n"
            "🤬 **Без мату:** Дотримуємося ввічливого спілкування.\n"
            "🛡️ **Без шахрайства:** Спроби скаму — негайний бан."
        )
    return (
        "📜 **Community Rules**\n\n"
        "🚫 **No Spam or Flooding:** Keep the chat clean.\n"
        "❌ **No Advertising:** Self-promotion or referral links are forbidden.\n"
        "🤝 **Respect & Courtesy:** Toxicity and insults will not be tolerated.\n"
        "🤬 **No Profanity:** Keep the language clean and polite.\n"
        "🛡️ **No Scams:** Fraudulent behavior leads to an immediate permanent ban."
    )

# --- ОБРОБНИК ВЕБХУКУ ВІД TELEGRAM (Команди та Кнопки) ---

@app.post("/telegram_webhook")
async def telegram_webhook(request: Request):
    """Обробляє команди від користувачів у Telegram (/start, натискання кнопок)"""
    try:
        data = await request.json()
        update = Update.de_json(data, bot)

        if not update:
            return {"status": "ok"}

        # 1. Обробка звичайних текстових команд (/start, /rules, /services)
        if update.message and update.message.text:
            text = update.message.text.strip()
            chat_id = update.message.chat_id

            if text in ["/start", "/services"]:
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_text_start("en"),
                    reply_markup=get_main_keyboard("en"),
                    parse_mode="Markdown"
                )
            elif text == "/rules":
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_text_rules("en"),
                    parse_mode="Markdown"
                )
            elif text == "/free_trial":
                await bot.send_message(
                    chat_id=chat_id,
                    text="🎁 **14-Day Free Trial Activated!**\n\nContact the admin to receive your access link.",
                    parse_mode="Markdown"
                )
            elif text == "/buy_group":
                await bot.send_message(
                    chat_id=chat_id,
                    text="📊 **VIP Signals Group ($20/mo)**\n\nPlease contact our support/admin to proceed with payment.",
                    parse_mode="Markdown"
                )
            elif text == "/connect_bot":
                await bot.send_message(
                    chat_id=chat_id,
                    text="🤖 **Signal Bot Connection ($100/mo)**\n\nPlease fill out this short form or contact admin to setup your bot connection.",
                    parse_mode="Markdown"
                )

        # 2. Обробка натискань на Inline-кнопки (Callback Query)
        elif update.callback_query:
            query = update.callback_query
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            data = query.data

            await bot.answer_callback_query(callback_query_id=query.id)

            if data == "lang_ua":
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=get_text_start("ua"),
                    reply_markup=get_main_keyboard("ua"),
                    parse_mode="Markdown"
                )
            elif data == "lang_en":
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=get_text_start("en"),
                    reply_markup=get_main_keyboard("en"),
                    parse_mode="Markdown"
                )
            elif data == "btn_services":
                await bot.send_message(chat_id=chat_id, text=get_text_services("en"), parse_mode="Markdown")
            elif data == "btn_rules":
                await bot.send_message(chat_id=chat_id, text=get_text_rules("en"), parse_mode="Markdown")
            elif data == "btn_free_trial":
                await bot.send_message(chat_id=chat_id, text="🎁 **14-Day Free Trial!** Contact admin to get your personal invite link.", parse_mode="Markdown")
            elif data == "btn_buy_group":
                await bot.send_message(chat_id=chat_id, text="📊 **VIP Signals Group Access ($20/mo)**\n\nContact admin to pay and get instant access.", parse_mode="Markdown")
            elif data == "btn_connect_bot":
                await bot.send_message(chat_id=chat_id, text="🤖 **Signal Bot Setup ($100/mo)**\n\nPlease send your exchange API details or write to admin to setup.", parse_mode="Markdown")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}")
        return {"status": "error", "message": str(e)}

# --- ТВОЇ ІСНУЮЧІ РОУТИ ДЛЯ TRADINGVIEW ТА ЗВІТІВ ---

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
