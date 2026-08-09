import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
import aiosqlite

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # ID вашої VIP-групи (-1003940810691)
DB_PATH = "trades.db"

# ⬇️ ВКАЖІТЬ ВАШІ АДРЕСИ КРИПТОГАМАНЦІВ BINANCE ⬇️
WALLET_USDT_TRC20 = "THeVYP6zqgJ3jKMhNAuBxqGk47iFno6pKL"
WALLET_USDT_BEP20 = "0x97eb6c4c2fe24798ccf24ed5d52cb228f32f5f5f"
WALLET_USDT_SOLANA = "5Pcc4WUfA1qBas6P42WDYRre8ugAenNe5UsN6c2DyUox"

app = FastAPI()
bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

# --- БАЗА ДАНИХ ---

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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                trial_used INTEGER DEFAULT 0,
                trial_start DATETIME,
                trial_end DATETIME,
                status TEXT DEFAULT 'free'
            )
        """)
        await db.commit()

# --- ФОНОВИЙ ТАЙМЕР ДЛЯ ВИДАЛЕННЯ ПІСЛЯ 14 ДНІВ ---

async def check_expired_trials():
    """Фонова задача: щогодини перевіряє, у кого закінчилися 14 днів, видаляє з каналу та надсилає пропозицію"""
    while True:
        try:
            await asyncio.sleep(3600)  # Перевірка кожну годину
            now = datetime.now(timezone.utc)
            
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute(
                    "SELECT user_id, username FROM users WHERE status = 'trial' AND trial_end <= ?",
                    (now.isoformat(),)
                ) as cursor:
                    expired_users = await cursor.fetchall()

                for user_id, username in expired_users:
                    try:
                        await bot.ban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                        await bot.unban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                        
                        await db.execute(
                            "UPDATE users SET status = 'expired' WHERE user_id = ?",
                            (user_id,)
                        )
                        await db.commit()

                        expired_keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📊 Оформити VIP ($20/міс)", callback_data="btn_buy_group")],
                            [InlineKeyboardButton("🤖 Підключити Signal Bot ($100/міс)", callback_data="btn_connect_bot")]
                        ])

                        await bot.send_message(
                            chat_id=user_id,
                            text=(
                                "⏳ **Ваш 14-денний тестовий період завершився!**\n\n"
                                "Сподіваємося, ви оцінили точність та якість сигналів **Mireya**! 🚀\n\n"
                                "Щоб не втрачати прибуткові угоди та продовжити отримувати сигнали в реальному часі, "
                                "оберіть один із варіантів продовження підписки нижче:"
                            ),
                            reply_markup=expired_keyboard,
                            parse_mode="Markdown"
                        )
                        logger.info(f"User {user_id} ({username}) removed after trial expiration.")
                    except Exception as e:
                        logger.error(f"Failed to remove expired user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error in check_expired_trials loop: {e}")

@app.on_event("startup")
async def startup_event():
    await init_db()
    asyncio.create_task(check_expired_trials())

# --- ДОПОМІЖНІ ФУНКЦІЇ ДЛЯ КНОПОК ТА МЕНЮ ---

def get_main_keyboard(lang="ua"):
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

def get_payment_keyboard():
    """Клавіатура після надсилання реквізитів (лише кнопка повернення назад)"""
    keyboard = [
        [InlineKeyboardButton("🔙 Повернутися в меню", callback_data="btn_back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payment_details_text():
    """Текст з адресами гаманців Binance та інструкцією без прямого посилання на адміна"""
    return (
        "💳 **Оплата підписки на VIP-групу ($20 / місяць)**\n\n"
        "Для активації підписки перекажіть **20 USDT** на один із гаманців Binance нижче:\n\n"
        f"🔸 **USDT (TRC20):**\n`{WALLET_USDT_TRC20}`\n\n"
        f"🔹 **USDT (BEP20 / BNB Chain):**\n`{WALLET_USDT_BEP20}`\n\n"
        f"🟣 **USDT (Solana):**\n`{WALLET_USDT_SOLANA}`\n\n"
        "*(Натисніть на адресу, щоб її скопіювати)*\n\n"
        "ℹ️ **Зверніть увагу:**\n"
        "Після підтвердження транзакції в мережі доступ до VIP-групи буде наданий **протягом дня**."
    )

def get_text_start(lang="ua"):
    if lang == "ua":
        return (
            "👋 **Вітаємо у спільноті!**\n\n"
            "Я — **Mireya**, твій персональний помічник із підключення та навігації. "
            "Ознайомся з нашими послугами, бонусами та правилами нижче!\n\n"
            "🎁 **Спеціальні пропозиції та Бонуси**\n"
            "• 🚀 **14 днів FREE-доступу:** Кожен новий користувач отримує 2 тижні безкоштовного тестового доступу до VIP-групи!\n"
            "• 👥 **Реферальна програма «Приведи друга»:** За кожного друга, який придбає підписку — отримуй **+14 днів безкоштовного доступу**!\n\n"
            "💎 **Наші Послуги та Прайс**\n"
            "• 📊 **VIP-група з сигналами:** **$20 / місяць** *(Аналітика ринку, торгові сигнали та чат спільноти)*\n"
            "• 🤖 **Персональний Signal Bot:** **$100 / місяць** *(Автоматичне підключення бота для миттєвого виконання сигналів)*\n\n"
            "📜 **Правила спільноти**\n"
            "• 🚫 Без спаму, флуду, реклами та реферальних посилань.\n"
            "• 🤝 Ввічливе спілкування, без мату та токсичності.\n"
            "• 🛡️ Шахрайство = негайний бан.\n\n"
            "👇 **Обери потрібну дію з меню нижче:**"
        )
    return (
        "👋 **Welcome to the community!**\n\n"
        "I am **Mireya**, your personal setup and navigation assistant. "
        "Please check out our services, bonuses, and community rules below!\n\n"
        "🎁 **Special Offers & Bonuses**\n"
        "• 🚀 **14-Day FREE Trial:** Every new user gets 2 weeks of free trial access to our VIP Signals Group!\n"
        "• 👥 **\"Refer a Friend\" Program:** Bring a friend, and once they subscribe, get **+14 days of free VIP access**!\n\n"
        "💎 **Services & Pricing**\n"
        "• 📊 **VIP Signals Group Access:** **$20 / month** *(Market analytics, trade signals, and community access)*\n"
        "• 🤖 **Personal Signal Bot Setup:** **$100 / month** *(Direct automated bot connection for instant signal execution)*\n\n"
        "📜 **Community Rules**\n"
        "• 🚫 No spam, flooding, self-promotion, or referral links.\n"
        "• 🤝 Respectful communication, no profanity or toxicity.\n"
        "• 🛡️ Fraudulent behavior results in an immediate permanent ban.\n\n"
        "👇 **Choose an option from the menu below:**"
    )

def get_text_services(lang="ua"):
    if lang == "ua":
        return (
            "💎 **Наші Послуги та Прайс**\n\n"
            "📊 **VIP-група з сигналами:** **$20 / місяць**\n\n"
            "🤖 **Персональний Signal Bot:** **$100 / місяць**\n\n"
            "🎁 **Бонуси:**\n"
            "• **14 днів FREE** для нових користувачів!\n"
            "• **+14 днів** за кожного друга, який придбає підписку!"
        )
    return (
        "💎 **Services & Pricing**\n\n"
        "📊 **VIP Signals Group Access:** **$20 / month**\n\n"
        "🤖 **Personal Signal Bot Setup:** **$100 / month**\n\n"
        "🎁 **Bonuses:**\n"
        "• **14-Day FREE Trial** for new users!\n"
        "• **+14 Days Free Access** for every referred friend who subscribes!"
    )

def get_text_rules(lang="ua"):
    if lang == "ua":
        return (
            "📜 **Правила спільноти**\n\n"
            "🚫 **Без спаму та флуду:** Масові розсилки заборонені.\n"
            "❌ **Заборона реклами:** Реклама без дозволу заборонена.\n"
            "🤝 **Повага та етика:** Образи та токсичність неприпустимі.\n"
            "🤬 **Без нецензурної лексики:** Дотримуємося ввічливого спілкування.\n"
            "🛡️ **Без шахрайства:** Спроби скаму = бан."
        )
    return (
        "📜 **Community Rules**\n\n"
        "🚫 **No Spam or Flooding:** Mass messaging is prohibited.\n"
        "❌ **No Advertising:** Self-promotion is forbidden.\n"
        "🤝 **Respect & Courtesy:** Toxicity will not be tolerated.\n"
        "🤬 **No Profanity:** Keep communication polite and clean.\n"
        "🛡️ **No Scams:** Immediate permanent ban."
    )

# --- ЛОГІКА ВИДАЧІ FREE ТРИАЛУ ---

async def handle_free_trial_request(user_id: int, username: str):
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=14)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT trial_used, status FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if user and user[0] == 1:
            return "⚠️ **Ви вже використовували безкоштовний 14-денний період.**\n\nЯкщо бажаєте продовжити доступ, ви можете оформити підписку ($20/міс) у головному меню."

        try:
            invite_link = await bot.create_chat_invite_link(
                chat_id=TELEGRAM_CHANNEL_ID,
                member_limit=1,
                expire_date=int((now + timedelta(hours=24)).timestamp())
            )
            
            await db.execute("""
                INSERT INTO users (user_id, username, trial_used, trial_start, trial_end, status)
                VALUES (?, ?, 1, ?, ?, 'trial')
                ON CONFLICT(user_id) DO UPDATE SET
                    trial_used = 1,
                    trial_start = excluded.trial_start,
                    trial_end = excluded.trial_end,
                    status = 'trial'
            """, (user_id, username, now.isoformat(), trial_end.isoformat()))
            await db.commit()

            return (
                f"🎉 **Вам надано 14 днів безкоштовного доступу!**\n\n"
                f"🔗 **Ваше одноразове посилання для входу:**\n{invite_link.invite_link}\n\n"
                f"⏰ Доступ буде активний до: **{trial_end.strftime('%Y-%m-%d %H:%M UTC')}**\n"
                f"*(Посилання дійсне 24 години, використайте його зараз)*"
            )
        except Exception as e:
            logger.error(f"Error creating invite link for user {user_id}: {e}")
            return "❌ **Помилка при створенні посилання.** Переконайтеся, що бот доданий у групу як адміністратор."

# --- ОБРОБНИК ВЕБХУКУ ВІД TELEGRAM ---

@app.post("/telegram_webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)

        if not update:
            return {"status": "ok"}

        # 1. Текстові команди
        if update.message and update.message.text:
            text = update.message.text.strip()
            chat_id = update.message.chat_id
            user_id = update.message.from_user.id
            username = update.message.from_user.username or "NoUsername"

            if text in ["/start", "/services"]:
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_text_start("ua"),
                    reply_markup=get_main_keyboard("ua"),
                    parse_mode="Markdown"
                )
            elif text == "/rules":
                await bot.send_message(chat_id=chat_id, text=get_text_rules("ua"), parse_mode="Markdown")
            elif text == "/free_trial":
                response_text = await handle_free_trial_request(user_id, username)
                await bot.send_message(chat_id=chat_id, text=response_text, parse_mode="Markdown")
            elif text == "/buy_group":
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_payment_details_text(),
                    reply_markup=get_payment_keyboard(),
                    parse_mode="Markdown"
                )
            elif text == "/connect_bot":
                await bot.send_message(
                    chat_id=chat_id,
                    text="🤖 **Підключення Signal Bot ($100/міс)**\n\nДля налаштування автоматичного виконання сигналів через бота зверніться в підтримку.",
                    parse_mode="Markdown"
                )

        # 2. Натискання на Inline-кнопки
        elif update.callback_query:
            query = update.callback_query
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            user_id = query.from_user.id
            username = query.from_user.username or "NoUsername"
            data = query.data

            await bot.answer_callback_query(callback_query_id=query.id)

            if data == "lang_ua":
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start("ua"), reply_markup=get_main_keyboard("ua"), parse_mode="Markdown")
            elif data == "lang_en":
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start("en"), reply_markup=get_main_keyboard("en"), parse_mode="Markdown")
            elif data == "btn_back_main":
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start("ua"), reply_markup=get_main_keyboard("ua"), parse_mode="Markdown")
            elif data == "btn_services":
                await bot.send_message(chat_id=chat_id, text=get_text_services("ua"), parse_mode="Markdown")
            elif data == "btn_rules":
                await bot.send_message(chat_id=chat_id, text=get_text_rules("ua"), parse_mode="Markdown")
            elif data == "btn_free_trial":
                response_text = await handle_free_trial_request(user_id, username)
                await bot.send_message(chat_id=chat_id, text=response_text, parse_mode="Markdown")
            elif data == "btn_buy_group":
                await bot.send_message(
                    chat_id=chat_id,
                    text=get_payment_details_text(),
                    reply_markup=get_payment_keyboard(),
                    parse_mode="Markdown"
                )
            elif data == "btn_connect_bot":
                await bot.send_message(
                    chat_id=chat_id,
                    text="🤖 **Підключення Signal Bot ($100/міс)**\n\nДля налаштування персонального бота зверніться до адміністратора.",
                    parse_mode="Markdown"
                )

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}")
        return {"status": "error", "message": str(e)}

# --- РОУТИ ДЛЯ TRADINGVIEW ТА ЗВІТІВ ---

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
