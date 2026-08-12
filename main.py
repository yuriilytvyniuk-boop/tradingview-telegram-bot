import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
import aiosqlite
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ЗМІННІ ОТОЧЕННЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # ID VIP-групи або каналу
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))  # ID адміна

# 🔗 Посилання на загальну групу спілкування
PUBLIC_CHAT_LINK = os.getenv("PUBLIC_CHAT_LINK", "https://t.me/kerdos_group")

# Endpoint OKX для прийому сигналів бота
OKX_SIGNAL_WEBHOOK_URL = "https://www.okx.com/priapi/v5/rubik/stat/trading-bot/signal/generic"

DB_PATH = "trades.db"

# ⬇️ РЕКВІЗИТИ КРИПТОГАМАНЦІВ BINANCE ⬇️
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
                sub_end DATETIME,
                bot_sub_end DATETIME,
                signal_token TEXT,
                status TEXT DEFAULT 'free',
                lang TEXT DEFAULT 'ua',
                referrer_id INTEGER DEFAULT NULL,
                awaiting_support INTEGER DEFAULT 0
            )
        """)
        await db.commit()

async def get_user_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
    return "ua"

async def set_user_lang(user_id: int, lang: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, lang)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang
        """, (user_id, lang))
        await db.commit()

async def set_awaiting_support(user_id: int, state: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, awaiting_support)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET awaiting_support = excluded.awaiting_support
        """, (user_id, state))
        await db.commit()

async def get_awaiting_support(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT awaiting_support FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] is not None:
                return row[0]
    return 0

# --- ФОНОВИЙ ТАЙМЕР ЗВІЛЬНЕННЯ ТРИАЛУ ТА ПІДПИСКИ ---

async def check_expired_trials():
    while True:
        try:
            await asyncio.sleep(3600)
            now = datetime.now(timezone.utc)
            
            async with aiosqlite.connect(DB_PATH) as db:
                # 1. Завершення триалу (14 днів)
                async with db.execute(
                    "SELECT user_id, username, lang FROM users WHERE status = 'trial' AND trial_end <= ?",
                    (now.isoformat(),)
                ) as cursor:
                    expired_trials = await cursor.fetchall()

                for user_id, username, lang in expired_trials:
                    try:
                        if TELEGRAM_CHANNEL_ID:
                            await bot.ban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                            await bot.unban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                        
                        await db.execute("UPDATE users SET status = 'expired' WHERE user_id = ?", (user_id,))
                        await db.commit()

                        user_lang = lang or "ua"
                        text = (
                            "⏳ **Ваш 14-денний тестовий період завершився!**\n\n"
                            "Сподіваємося, ви оцінили точність та якість сигналів **Kerdos**! 🚀\n\n"
                            "Щоб продовжити отримувати сигнали в реальному часі, оберіть варіант підписки нижче:"
                            if user_lang == "ua" else
                            "⏳ **Your 14-day free trial has expired!**\n\n"
                            "We hope you enjoyed the signal quality of **Kerdos**! 🚀\n\n"
                            "To keep receiving real-time signals, please select a subscription option below:"
                        )

                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=get_main_keyboard(user_lang),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to remove expired user {user_id}: {e}")

                # 2. Завершення платній підписки на VIP-групу
                async with db.execute(
                    "SELECT user_id, username, lang FROM users WHERE status = 'active' AND sub_end <= ?",
                    (now.isoformat(),)
                ) as cursor:
                    expired_subs = await cursor.fetchall()

                for user_id, username, lang in expired_subs:
                    try:
                        if TELEGRAM_CHANNEL_ID:
                            await bot.ban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                            await bot.unban_chat_member(chat_id=TELEGRAM_CHANNEL_ID, user_id=user_id)
                        
                        await db.execute("UPDATE users SET status = 'expired' WHERE user_id = ?", (user_id,))
                        await db.commit()

                        user_lang = lang or "ua"
                        text = (
                            "⏳ **Термін вашої підписки на VIP-групу Kerdos закінчився.**\n\nДля продовження підписки скористайтеся меню бота."
                            if user_lang == "ua" else
                            "⏳ **Your Kerdos VIP group subscription has expired.**\n\nPlease use the menu to renew your access."
                        )

                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=get_main_keyboard(user_lang),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to remove expired sub user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error in check_expired_trials loop: {e}")

@app.on_event("startup")
async def startup_event():
    await init_db()
    asyncio.create_task(check_expired_trials())

# --- КНОПКИ ТА МЕНЮ ---

def get_main_keyboard(lang="ua"):
    if lang == "ua":
        keyboard = [
            [InlineKeyboardButton("⏳ Моя підписка", callback_data="btn_my_sub")],
            [InlineKeyboardButton("🎁 Отримати 14 днів FREE", callback_data="btn_free_trial")],
            [InlineKeyboardButton("👥 Реферальна програма", callback_data="btn_referral")],
            [InlineKeyboardButton("📊 Доступ до VIP-групи ($20 / 30 днів)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Підключити Signal Bot ($100 / 30 днів)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Послуги та ціни", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Правила спільноти", callback_data="btn_rules")],
            [InlineKeyboardButton("🛟 Підтримка / Допомога", callback_data="btn_support")],
            [InlineKeyboardButton("💬 Чат спільноти", url=PUBLIC_CHAT_LINK)],
            [InlineKeyboardButton("🇬🇧 Switch to English", callback_data="lang_en")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("⏳ My Subscription", callback_data="btn_my_sub")],
            [InlineKeyboardButton("🎁 Get 14-Day Free Trial", callback_data="btn_free_trial")],
            [InlineKeyboardButton("👥 Referral Program", callback_data="btn_referral")],
            [InlineKeyboardButton("📊 VIP Signals Group Access ($20 / 30 days)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Connect Signal Bot ($100 / 30 days)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Services & Pricing", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Community Rules", callback_data="btn_rules")],
            [InlineKeyboardButton("🛟 Support / Help", callback_data="btn_support")],
            [InlineKeyboardButton("💬 Community Chat", url=PUBLIC_CHAT_LINK)],
            [InlineKeyboardButton("🇺🇦 Переключити на Українську", callback_data="lang_ua")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(lang="ua"):
    back_text = "🔙 Повернутися в меню" if lang == "ua" else "🔙 Back to Menu"
    return InlineKeyboardMarkup([[InlineKeyboardButton(back_text, callback_data="btn_back_main")]])

def get_cancel_support_keyboard(lang="ua"):
    cancel_text = "❌ Скасувати звернення" if lang == "ua" else "❌ Cancel Support Request"
    return InlineKeyboardMarkup([[InlineKeyboardButton(cancel_text, callback_data="btn_cancel_support")]])

# =======================================================
# БЛОК ДОДАНОГО ФУНКЦІОНАЛУ: КЛАВІАТУРА АДМІН-ПАНЕЛІ
# =======================================================
def get_admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Список підключених людей", callback_data="admin_users_list")],
        [InlineKeyboardButton("👑 Надати VIP", callback_data="admin_grant_vip")],
        [InlineKeyboardButton("🤖 Надати доступ до бота", callback_data="admin_grant_bot")]
    ])
# =======================================================

# =======================================================
# БЛОК ДОДАНОГО ФУНКЦІОНАЛУ: ПІДРАХУНОК ДНІВ, ЩО ЗАЛИШИЛИСЬ
# =======================================================
def calc_days_left(end_iso: str) -> int:
    """Повертає кількість повних днів, що залишились до end_iso (0, якщо термін минув)."""
    try:
        end_dt = datetime.fromisoformat(end_iso)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = end_dt - now
        if delta.total_seconds() <= 0:
            return 0
        # Округлюємо вгору, щоб "залишилось менше доби" не показувало 0
        days = delta.days + (1 if delta.seconds > 0 or delta.microseconds > 0 else 0)
        return max(days, 0)
    except Exception:
        return 0
# =======================================================

# --- ТЕКСТИ ПОВІДОМЛЕНЬ ---

def get_text_start(lang="ua"):
    if lang == "ua":
        return (
            "👋 **Вітаємо у спільноті Kerdos!**\n\n"
            "Я — **Mireya**, ваш персональний помічник аналітичної торгової системи **Kerdos**.\n\n"
            "🎁 **Спеціальні пропозиції та Бонуси:**\n"
            "• 🚀 **14 днів FREE-доступу:** Кожен новий користувач отримує 2 тижні безкоштовного тестового доступу до VIP-групи Kerdos!\n"
            "• 👥 **Реферальна програма «Приведи друга»:** За кожного друга, який візьме безкоштовний пробний період — отримуй **+14 днів безкоштовного доступу**!\n\n"
            "💎 **Наші Послуги та Прайс:**\n"
            "• 📊 **VIP-група з сигналами Kerdos:** **$20 / 30 днів** *(Аналітика ринку, торгові сигнали та чат спільноти)*\n"
            "• 🤖 **Персональний Signal Bot:** **$100 / 30 днів** *(Автоматичне підключення вашого акаунту OKX для миттєвої торгівлі)*\n\n"
            "⚠️ **Управління ризиками та відповідальність:**\n"
            "• 📈 Торгівля на криптовалютному ринку завжди пов'язана з високими ризиками.\n"
            "• 🛡️ Обов'язково дотримуйтесь суворого **ризик- та мані-менеджменту** — контролюйте розмір плеча та закладайте безпечний відсоток депозиту на одну угоду.\n"
            "• ⚖️ Ми **не несемо відповідальності** за ваш баланс та фінансові результати — ви повністю контролюєте власні кошти та самостійно приймаєте рішення.\n"
            "• 🔥 Проте при дотриманні дисципліни, системного підходу та правил стратегії — це дає чудові результати!\n\n"
            "📜 **Правила спільноти:**\n"
            "• 🚫 Без спаму, флуду, реклами та реферальних посилань.\n"
            "• 🤝 Ввічливе спілкування, без мату та токсичності.\n"
            "• 🛡️ Шахрайство = негайний бан.\n\n"
            "👇 **Обери потрібну дію з меню нижче:**"
        )
    return (
        "👋 **Welcome to the Kerdos community!**\n\n"
        "I am **Mireya**, your personal assistant for the **Kerdos** trading system.\n\n"
        "🎁 **Special Offers & Bonuses:**\n"
        "• 🚀 **14-Day FREE Trial:** Every new user gets 2 weeks of free trial access to our Kerdos VIP Signals Group!\n"
        "• 👥 **\"Refer a Friend\" Program:** Bring a friend, and once they claim their free trial, get **+14 days of free VIP access**!\n\n"
        "💎 **Services & Pricing:**\n"
        "• 📊 **Kerdos VIP Signals Group:** **$20 / 30 days** *(Market analytics, trade signals, and community access)*\n"
        "• 🤖 **Personal Signal Bot Setup:** **$100 / 30 days** *(Direct OKX bot connection for automated signal execution)*\n\n"
        "⚠️ **Risk Management & Disclaimer:**\n"
        "• 📈 Cryptocurrency trading involves substantial financial risk.\n"
        "• 🛡️ Always practice strict **risk and money management** — control your leverage and allocate a safe percentage of your capital per trade.\n"
        "• ⚖️ We **are not responsible** for your balance or trading outcomes — you maintain full control over your funds and make decisions independently.\n"
        "• 🔥 However, with proper discipline and strategic rule execution, it yields excellent long-term results!\n\n"
        "📜 **Community Rules:**\n"
        "• 🚫 No spam, flooding, self-promotion, or referral links.\n"
        "• 🤝 Respectful communication, no profanity or toxicity.\n"
        "• 🛡️ Fraudulent behavior results in an immediate permanent ban.\n\n"
        "👇 **Choose an option from the menu below:**"
    )

def get_text_support_prompt(lang="ua"):
    if lang == "ua":
        return (
            "🛟 **СЛУЖБА ПІДТРИМКИ KERDOS**\n\n"
            "Ви виявили помилку, маєте запитання щодо підписки або потребуєте допомоги з налаштуванням?\n\n"
            "📝 **Будь ласка, опишіть вашу проблему нижче в одному повідомленні:**\n"
            "*(Ви також можете додати скріншот або фото помилки)*\n\n"
            "⏳ *Mireya одразу ж передасть ваше звернення адміністратору!*"
        )
    return (
        "🛟 **KERDOS SUPPORT HELPDESK**\n\n"
        "Did you encounter an issue, have questions about your subscription, or need setup assistance?\n\n"
        "📝 **Please describe your issue below in a single message:**\n"
        "*(You can also attach a screenshot or photo)*\n\n"
        "⏳ *Mireya will forward your ticket directly to the administrator!*"
    )

def get_text_vip_payment(lang="ua"):
    if lang == "ua":
        return (
            "💳 **Оплата підписки на VIP-групу Kerdos ($20 / 30 днів)**\n\n"
            "Для активації підписки перекажіть **20 USDT** на один із гаманців Binance нижче:\n\n"
            f"🔸 **USDT (TRC20):**\n`{WALLET_USDT_TRC20}`\n\n"
            f"🔹 **USDT (BEP20 / BNB Chain):**\n`{WALLET_USDT_BEP20}`\n\n"
            f"🟣 **USDT (Solana):**\n`{WALLET_USDT_SOLANA}`\n\n"
            "*(Натисніть на адресу, щоб її скопіювати)*\n\n"
            "📥 **ПІДТВЕРДЖЕННЯ ОПЛАТИ:**\n"
            "Після виконання переказу **надішліть квитанцію (фото, скріншот або текст з хешем транзакції) сюди в чат**.\n\n"
            "Я (Mireya) передам її адміністратору на перевірку, і доступ буде надано!"
        )
    return (
        "💳 **Kerdos VIP Group Subscription ($20 / 30 days)**\n\n"
        "To activate your subscription, send **20 USDT** to one of the Binance wallets below:\n\n"
        f"🔸 **USDT (TRC20):**\n`{WALLET_USDT_TRC20}`\n\n"
        f"🔹 **USDT (BEP20 / BNB Chain):**\n`{WALLET_USDT_BEP20}`\n\n"
        f"🟣 **USDT (Solana):**\n`{WALLET_USDT_SOLANA}`\n\n"
        "*(Tap the address to copy it)*\n\n"
        "📥 **HOW TO CONFIRM PAYMENT:**\n"
        "After completing the transfer, **send the receipt (photo, screenshot, or transaction TxID) directly into this chat**.\n\n"
        "I (Mireya) will forward it to the admin for verification!"
    )

def get_text_bot_payment(lang="ua"):
    if lang == "ua":
        return (
            "🤖 **Підключення Kerdos Signal Bot ($100 / 30 днів)**\n\n"
            "Персональний бот для автоматичного виконання сигналів **Kerdos** на вашому акаунті OKX.\n\n"
            "⚡ **Переваги:**\n"
            "• Автоматичне відкриття/закриття угод 24/7\n"
            "• Без передачі API-ключів (безпечно через Signal Token)\n"
            "• Миттєва швидкість виконання сигналів\n\n"
            "💳 **Вартість:** **$100 / 30 днів**\n\n"
            "Перекажіть **100 USDT** на один із гаманців Binance:\n\n"
            f"🔸 **USDT (TRC20):**\n`{WALLET_USDT_TRC20}`\n\n"
            f"🔹 **USDT (BEP20 / BNB Chain):**\n`{WALLET_USDT_BEP20}`\n\n"
            f"🟣 **USDT (Solana):**\n`{WALLET_USDT_SOLANA}`\n\n"
            "📥 **ПІДТВЕРДЖЕННЯ ОПЛАТИ:**\n"
            "Після переказу **надішліть квитанцію (скріншот або хеш) сюди в чат**."
        )
    return (
        "🤖 **Connect Kerdos Signal Bot ($100 / 30 days)**\n\n"
        "Automated bot for executing **Kerdos** signals directly on your OKX account.\n\n"
        "⚡ **Benefits:**\n"
        "• 24/7 automated trade execution\n"
        "• Safe setup without sharing API keys (via Signal Token)\n"
        "• Instant signal execution speed\n\n"
        "💳 **Price:** **$100 / 30 days**\n\n"
        "Send **100 USDT** to one of the Binance wallets below:\n\n"
        f"🔸 **USDT (TRC20):**\n`{WALLET_USDT_TRC20}`\n\n"
        f"🔹 **USDT (BEP20 / BNB Chain):**\n`{WALLET_USDT_BEP20}`\n\n"
        f"🟣 **USDT (Solana):**\n`{WALLET_USDT_SOLANA}`\n\n"
        "📥 **HOW TO CONFIRM PAYMENT:**\n"
        "After transferring, **send your receipt (photo, screenshot, or TxID) into this chat**."
    )

def get_text_services(lang="ua"):
    if lang == "ua":
        return (
            "💎 **Наші Послуги та Прайс (Kerdos)**\n\n"
            "📊 **VIP-група з сигналами:** **$20 / 30 днів**\n\n"
            "🤖 **Персональний Signal Bot:** **$100 / 30 днів**\n\n"
            "🎁 **Бонуси:**\n"
            "• **14 днів FREE** для нових користувачів!\n"
            "• **+14 днів** за кожного друга, який візьме безкоштовний пробний період!"
        )
    return (
        "💎 **Services & Pricing (Kerdos)**\n\n"
        "📊 **VIP Signals Group Access:** **$20 / 30 days**\n\n"
        "🤖 **Personal Signal Bot Setup:** **$100 / 30 days**\n\n"
        "🎁 **Bonuses:**\n"
        "• **14-Day FREE Trial** for new users!\n"
        "• **+14 Days Free Access** for every referred friend who claims their free trial!"
    )

def get_text_rules(lang="ua"):
    if lang == "ua":
        return (
            "📜 **Правила спільноти Kerdos**\n\n"
            "🚫 **Без спаму та флуду:** Масові розсилки заборонені.\n"
            "❌ **Заборона реклами:** Реклама без дозволу заборонена.\n"
            "🤝 **Повага та етика:** Образи та токсичність неприпустимі.\n"
            "🤬 **Без нецензурної лексики:** Дотримуємося ввічливого спілкування.\n"
            "🛡️ **Без шахрайства:** Спроби скаму = бан."
        )
    return (
        "📜 **Kerdos Community Rules**\n\n"
        "🚫 **No Spam or Flooding:** Mass messaging is prohibited.\n"
        "❌ **No Advertising:** Self-promotion is forbidden.\n"
        "🤝 **Respect & Courtesy:** Toxicity will not be tolerated.\n"
        "🤬 **No Profanity:** Keep communication polite and clean.\n"
        "🛡️ **No Scams:** Immediate permanent ban."
    )

def get_text_okx_instruction(lang="ua"):
    if lang == "ua":
        return (
            "🎉 **Оплату Kerdos Signal Bot підтверджено!**\n\n"
            "Для підключення вашого акаунту OKX до системи сигналів **Kerdos**, будь ласка, надайте ваш **Signal Token**.\n\n"
            "📍 **Де знайти Signal Token на OKX:**\n"
            "1. Зайдіть на біржу **OKX** ➔ розділ **Торгувати (Trade)** ➔ **Торгові боти (Trading Bots)**.\n"
            "2. Оберіть **Сигнальний бот (Signal Bot)** ➔ **Створити власні сигнали (Create Custom Signal)**.\n"
            "3. Введіть назву сигналу (наприклад, `Kerdos Signals`) та натисніть **Створити**.\n"
            "4. Скопіюйте рядок **Signal Token** з налаштувань бота.\n\n"
            "📥 **Надішліть ваш токен у цей чат у такому форматі:**\n"
            "`Token: ваш_signal_token_тут`"
        )
    return (
        "🎉 **Kerdos Signal Bot payment approved!**\n\n"
        "To connect your OKX account to the **Kerdos** signal system, please provide your **Signal Token**.\n\n"
        "📍 **Where to find Signal Token on OKX:**\n"
        "1. Go to **OKX** ➔ **Trade** ➔ **Trading Bots**.\n"
        "2. Select **Signal Bot** ➔ **Create Custom Signal**.\n"
        "3. Name your signal (e.g., `Kerdos Signals`) and click **Create**.\n"
        "4. Copy the **Signal Token** string from the bot settings.\n\n"
        "📥 **Send your token in this chat using the format:**\n"
        "`Token: your_signal_token_here`"
    )

# --- РЕФЕРАЛЬНА ПРОГРАМА ТА ЛОГІКА ТРИАЛУ ---

async def get_referral_text(user_id: int, bot_username: str, lang: str = "ua") -> str:
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ? AND trial_used = 1", (user_id,)) as cursor:
            row = await cursor.fetchone()
            active_refs = row[0] if row else 0

    if lang == "ua":
        return (
            "👥 **Реферальна програма Kerdos «Приведи друга»**\n\n"
            "Запрошуйте друзів та отримуйте **+14 днів безкоштовного доступу** до VIP-групи за кожного друга, який активує безкоштовний пробний період!\n\n"
            f"🔗 **Ваше персональне посилання:**\n`{ref_link}`\n\n"
            f"📊 **Ваші запрошені друзі, які взяли FREE-триал:** {active_refs}\n\n"
            "*(Натисніть на посилання, щоб скопіювати його та поділитися з друзями)*"
        )
    return (
        "👥 **Kerdos Referral Program \"Refer a Friend\"**\n\n"
        "Invite your friends and receive **+14 days of free VIP access** for every friend who activates their free trial!\n\n"
        f"🔗 **Your personal referral link:**\n`{ref_link}`\n\n"
        f"📊 **Friends who claimed FREE trial:** {active_refs}\n\n"
        "*(Tap the link to copy and share it with your friends)*"
    )

async def handle_free_trial_request(user_id: int, username: str, lang: str = "ua"):
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=14)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT trial_used, referrer_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if user and user[0] == 1:
            if lang == "ua":
                return "⚠️ **Ви вже використовували безкоштовний 14-денний період.**\n\nВи можете оформити підписку у головному меню."
            return "⚠️ **You have already used your 14-day free trial.**\n\nYou can subscribe in the main menu."

        referrer_id = user[1] if user else None

        try:
            if not TELEGRAM_CHANNEL_ID:
                return "❌ Помилка: Не налаштовано TELEGRAM_CHANNEL_ID."

            invite_link = await bot.create_chat_invite_link(
                chat_id=TELEGRAM_CHANNEL_ID,
                member_limit=1,
                expire_date=int((now + timedelta(hours=24)).timestamp())
            )
            
            await db.execute("""
                INSERT INTO users (user_id, username, trial_used, trial_start, trial_end, status, lang)
                VALUES (?, ?, 1, ?, ?, 'trial', ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    trial_used = 1,
                    trial_start = excluded.trial_start,
                    trial_end = excluded.trial_end,
                    status = 'trial'
            """, (user_id, username, now.isoformat(), trial_end.isoformat(), lang))
            await db.commit()

            # --- АВТОМАТИЧНЕ НАРАХУВАННЯ БОНУСУ ЗАПРОШУЮЧОМУ ---
            if referrer_id:
                async with db.execute("SELECT trial_end, sub_end, status, lang FROM users WHERE user_id = ?", (referrer_id,)) as cursor:
                    ref_user = await cursor.fetchone()

                if ref_user:
                    ref_trial_end, ref_sub_end, ref_status, ref_lang = ref_user
                    ref_lang = ref_lang or "ua"

                    if ref_status == 'active' and ref_sub_end:
                        curr_end = datetime.fromisoformat(ref_sub_end)
                        if curr_end.tzinfo is None:
                            curr_end = curr_end.replace(tzinfo=timezone.utc)
                        base_time = max(now, curr_end)
                        new_end = base_time + timedelta(days=14)
                        await db.execute("UPDATE users SET sub_end = ? WHERE user_id = ?", (new_end.isoformat(), referrer_id))
                    else:
                        curr_end = None
                        if ref_trial_end:
                            curr_end = datetime.fromisoformat(ref_trial_end)
                            if curr_end.tzinfo is None:
                                curr_end = curr_end.replace(tzinfo=timezone.utc)
                        
                        base_time = max(now, curr_end) if curr_end else now
                        new_end = base_time + timedelta(days=14)
                        await db.execute("UPDATE users SET trial_end = ?, status = 'trial' WHERE user_id = ?", (new_end.isoformat(), referrer_id))

                    await db.commit()

                    bonus_msg = (
                        f"🥳 **Ваш друг (@{username}) взяв безкоштовний тестовий період!**\n\n"
                        f"🎁 Вам автоматично нараховано **+14 днів безкоштовного доступу** до Kerdos VIP!\n"
                        f"⏰ Новий термін дії доступу: **{new_end.strftime('%Y-%m-%d %H:%M UTC')}**"
                        if ref_lang == "ua" else
                        f"🥳 **Your friend (@{username}) claimed their free trial!**\n\n"
                        f"🎁 You have automatically received **+14 free days** of Kerdos VIP access!\n"
                        f"⏰ New expiration date: **{new_end.strftime('%Y-%m-%d %H:%M UTC')}**"
                    )
                    try:
                        await bot.send_message(chat_id=referrer_id, text=bonus_msg, parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

            if lang == "ua":
                return (
                    f"🎉 **Вам надано 14 днів безкоштовного доступу до Kerdos VIP!**\n\n"
                    f"🔗 **Ваше одноразове посилання:**\n{invite_link.invite_link}\n\n"
                    f"⏰ Доступ активний до: **{trial_end.strftime('%Y-%m-%d %H:%M UTC')}**"
                )
            return (
                f"🎉 **You have been granted 14 days of free access to Kerdos VIP!**\n\n"
                f"🔗 **Your invite link:**\n{invite_link.invite_link}\n\n"
                f"⏰ Access valid until: **{trial_end.strftime('%Y-%m-%d %H:%M UTC')}**"
            )
        except Exception as e:
            logger.error(f"Error creating invite link for user {user_id}: {e}")
            return "❌ Помилка при створенні посилання. Переконайся, що Mireya додана у групу як адмін."

# --- РОЗСИЛКА СИГНАЛІВ НА OKX SIGNAL BOT ТА ЗВІТ АДМІНУ ---

async def send_signal_to_okx(tokens_info: list[tuple], ticker: str, action: str):
    if not tokens_info:
        logger.info("Немає активних підписників Signal Bot для відправки.")
        return

    # Очищаємо тикер від USDT, тире, .P та PERP для формування точного інструменту OKX
    formatted_ticker = ticker.replace("USDT", "").replace("-", "").replace(".P", "").replace("PERP", "")
    instrument = f"{formatted_ticker}-USDT-SWAP"

    okx_action = None
    if action in ["buy", "long"]:
        okx_action = "enter_long"
    elif action in ["sell", "short"]:
        okx_action = "enter_short"
    elif action in ["close", "exit"]:
        okx_action = "exit_long"

    if not okx_action:
        logger.warning(f"Незрозуміла дія для OKX: {action}")
        return

    success_users = []
    failed_users = []

    # 1. Налаштування OKX та Проксі Webshare
    OKX_SIGNAL_URL = "https://www.okx.com/algo/signal/trigger"
    PROXY_URL = "http://scjqxfsf:p1urqrmkjedm@31.59.20.176:6754"

    # 2. Повний комплект заголовків браузера проти блокування Cloudflare (403)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.okx.com",
        "Referer": "https://www.okx.com/"
    }

    # 3. Відправка запитів через проксі
    async with httpx.AsyncClient(proxy=PROXY_URL, timeout=10.0) as client:
        for user_id, username, token in tokens_info:
            user_disp = f"@{username}" if username and username != "None" else f"ID: {user_id}"
            payload = {
                "signalToken": token,
                "action": okx_action,
                "instrument": instrument
            }
            try:
                response = await client.post(OKX_SIGNAL_URL, json=payload, headers=headers)
                if response.status_code == 200:
                    logger.info(f"✅ Сигнал відправлено OKX для {user_disp}")
                    success_users.append(f"• {user_disp}")
                else:
                    logger.error(f"❌ Помилка OKX [{response.status_code}] для {user_disp}: {response.text}")
                    failed_users.append(f"• {user_disp} — Код: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Збій відправки OKX для {user_disp}: {e}")
                failed_users.append(f"• {user_disp} — {e}")

    # 4. Формування та відправка звіту в Telegram
    if ADMIN_TELEGRAM_ID and bot:
        clean_ticker = ticker.replace(".P", "")
        report = f"🤖 **ЗВІТ РОЗСИЛКИ OKX SIGNAL BOT**\n\n"
        report += f"📊 **Сигнал:** {action.upper()} #{clean_ticker}\n"
        report += f"🎯 **Дія OKX:** `{okx_action}`\n\n"
        report += f"✅ **Успішно виконано ({len(success_users)}):**\n"
        report += ("\n".join(success_users) if success_users else "Немає") + "\n\n"

        if failed_users:
            report += f"❌ **Помилки ({len(failed_users)}):**\n"
            report += "\n".join(failed_users)

        try:
            await bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=report,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Не вдалося надіслати звіт адміністратору: {e}")



# --- ВЕБХУК TELEGRAM ---

@app.post("/telegram_webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)

        if not update:
            return {"status": "ok"}

        # 1. ОБРОБКА ПОВІДОМЛЕНЬ
        if update.message:
            chat_id = update.message.chat_id
            user_id = update.message.from_user.id
            username = update.message.from_user.username or "no_username"
            user_lang = await get_user_lang(user_id)
            is_awaiting_support = await get_awaiting_support(user_id)

            # Обробка введення Signal Token (формат Token: xxx)
            if update.message.text and update.message.text.strip().lower().startswith("token:"):
                raw_token = update.message.text.strip().split(":", 1)[1].strip()
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET signal_token = ? WHERE user_id = ?", (raw_token, user_id))
                    await db.commit()

                success_text = (
                    "✅ **Signal Token успішно збережено!**\n\nВаш акаунт OKX прив'язано до системи сигналів **Kerdos**."
                    if user_lang == "ua" else
                    "✅ **Signal Token saved successfully!**\n\nYour OKX account is now connected to **Kerdos** signals."
                )
                await bot.send_message(chat_id=chat_id, text=success_text, parse_mode="Markdown")
                return {"status": "ok"}

            # Команди та рефералка
            if update.message.text and update.message.text.startswith("/"):
                text = update.message.text.strip()
                await set_awaiting_support(user_id, 0) # Скидаємо очікування підтримки при використанні команд

                if text.startswith("/start"):
                    args = text.split()
                    if len(args) > 1 and args[1].startswith("ref_"):
                        try:
                            ref_id = int(args[1].split("_")[1])
                            if ref_id != user_id:
                                async with aiosqlite.connect(DB_PATH) as db:
                                    await db.execute("""
                                        INSERT INTO users (user_id, username, referrer_id, lang)
                                        VALUES (?, ?, ?, ?)
                                        ON CONFLICT(user_id) DO UPDATE SET
                                            referrer_id = COALESCE(users.referrer_id, excluded.referrer_id)
                                    """, (user_id, username, ref_id, user_lang))
                                    await db.commit()
                        except ValueError:
                            pass

                    await bot.send_message(chat_id=chat_id, text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")
                    return {"status": "ok"}

                elif text == "/services":
                    await bot.send_message(chat_id=chat_id, text=get_text_services(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
                    return {"status": "ok"}
                elif text == "/rules":
                    await bot.send_message(chat_id=chat_id, text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
                    return {"status": "ok"}
                
                # =======================================================
                # БЛОК ДОДАНОГО ФУНКЦІОНАЛУ: АДМІН-КОМАНДИ
                # =======================================================
                elif text == "/admin" and ADMIN_TELEGRAM_ID and user_id == ADMIN_TELEGRAM_ID:
                    await bot.send_message(
                        chat_id=chat_id,
                        text="🛠 *Панель адміністратора:*\nОберіть потрібну дію нижче:",
                        reply_markup=get_admin_panel_keyboard(),
                        parse_mode="Markdown"
                    )
                    return {"status": "ok"}
                
                elif text.startswith("/give_vip") and ADMIN_TELEGRAM_ID and user_id == ADMIN_TELEGRAM_ID:
                    parts = text.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        target_user_id = int(parts[1])
                        now = datetime.now(timezone.utc)
                        new_end = now + timedelta(days=30)
                        
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE users SET status = 'VIP', sub_end = ? WHERE user_id = ?", 
                                (new_end.isoformat(), target_user_id)
                            )
                            await db.commit()
                            
                            async with db.execute("SELECT lang FROM users WHERE user_id = ?", (target_user_id,)) as cursor:
                                row = await cursor.fetchone()
                                target_lang = row[0] if row and row[0] else "ua"

                        user_msg = (
                            f"🎉 **Адміністратор надав вам VIP доступ на 30 днів!**"
                            if target_lang == "ua" else
                            f"🎉 **Admin granted you VIP access for 30 days!**"
                        )

                        try:
                            await bot.send_message(chat_id=target_user_id, text=user_msg, parse_mode="Markdown")
                            notification_status = "📤 Користувачу надіслано сповіщення."
                        except Exception as e:
                            notification_status = f"⚠️ Доступ оновлено в БД, але не вдалося написати користувачу: {e}"

                        await bot.send_message(
                            chat_id=chat_id, 
                            text=f"✅ Статус **VIP** на 30 днів надано користувачу `{target_user_id}`.\n\n{notification_status}", 
                            parse_mode="Markdown"
                        )
                    else:
                        await bot.send_message(chat_id=chat_id, text="Помилка. Використовуйте формат: `/give_vip 123456789`", parse_mode="Markdown")
                    return {"status": "ok"}
                
                elif text.startswith("/give_bot") and ADMIN_TELEGRAM_ID and user_id == ADMIN_TELEGRAM_ID:
                    parts = text.split()
                    if len(parts) == 2 and parts[1].isdigit():
                        target_user_id = int(parts[1])
                        now = datetime.now(timezone.utc)
                        new_end = now + timedelta(days=30)
                        
                        async with aiosqlite.connect(DB_PATH) as db:
                            await db.execute(
                                "UPDATE users SET status = 'BOT', bot_sub_end = ? WHERE user_id = ?", 
                                (new_end.isoformat(), target_user_id)
                            )
                            await db.commit()
                            
                            async with db.execute("SELECT lang, signal_token FROM users WHERE user_id = ?", (target_user_id,)) as cursor:
                                row = await cursor.fetchone()
                                target_lang = row[0] if row and row[0] else "ua"
                                user_token = row[1] if row else None

                        if user_token:
                            user_msg = (
                                f"🎉 **Адміністратор надав вам доступ до Kerdos Signal Bot на 30 днів!**\n\n"
                                f"✅ Ваш Signal Token вже наявний у системі, тож бот готовий до роботи!\n"
                                f"🔑 **Токен:** `{user_token[:6]}...{user_token[-4:]}`"
                                if target_lang == "ua" else
                                f"🎉 **Admin granted you Kerdos Signal Bot access for 30 days!**\n\n"
                                f"✅ Your Signal Token is already saved, the bot is ready!\n"
                                f"🔑 **Token:** `{user_token[:6]}...{user_token[-4:]}`"
                            )
                        else:
                            user_msg = get_text_okx_instruction(target_lang)

                        try:
                            await bot.send_message(chat_id=target_user_id, text=user_msg, parse_mode="Markdown")
                            notification_status = "📤 Користувачу надіслано сповіщення та інструкцію."
                        except Exception as e:
                            notification_status = f"⚠️ Доступ оновлено в БД, але не вдалося написати користувачу: {e}"

                        await bot.send_message(
                            chat_id=chat_id, 
                            text=f"✅ Статус **Bot** на 30 днів надано користувачу `{target_user_id}`.\n\n{notification_status}", 
                            parse_mode="Markdown"
                        )
                    else:
                        await bot.send_message(chat_id=chat_id, text="Помилка. Використовуйте формат: `/give_bot 123456789`", parse_mode="Markdown")
                    return {"status": "ok"}
                    
                # =======================================================

            # 📩 ОБРОБКА ЗВЕРНЕННЯ В ПІДТРИМКУ
            if is_awaiting_support == 1 and ADMIN_TELEGRAM_ID and user_id != ADMIN_TELEGRAM_ID:
                await set_awaiting_support(user_id, 0)
                
                admin_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Ввійти в чат / Відповісти", url=f"tg://user?id={user_id}")]
                ])

                support_header = f"🛟 **НОВЕ ЗВЕРНЕННЯ В ПІДТРИМКУ!**\n\n👤 **Від:** @{username}\n🆔 **ID:** `{user_id}`\n🌐 **Мова:** {user_lang.upper()}\n"

                if update.message.photo:
                    photo_file_id = update.message.photo[-1].file_id
                    caption_text = f"{support_header}\n📝 **Опис:**\n{update.message.caption or 'Без опису'}"
                    await bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=photo_file_id,
                        caption=caption_text,
                        reply_markup=admin_keyboard,
                        parse_mode="Markdown"
                    )
                elif update.message.text:
                    full_support_text = f"{support_header}\n📝 **Опис помилки:**\n{update.message.text}"
                    await bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=full_support_text,
                        reply_markup=admin_keyboard,
                        parse_mode="Markdown"
                    )

                confirm_text = (
                    "🚀 **Ваше звернення успішно передано адміністратору!**\n\nМи розглянемо його найближчим часом та зв'яжемося з вами."
                    if user_lang == "ua" else
                    "🚀 **Your support request has been delivered to the admin!**\n\nWe will review it and get back to you shortly."
                )
                await bot.send_message(chat_id=chat_id, text=confirm_text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
                return {"status": "ok"}

            # 💳 ОБРОБКА КВИТАНЦІЙ ПРО ОПЛАТУ
            if ADMIN_TELEGRAM_ID and user_id != ADMIN_TELEGRAM_ID:
                admin_keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Підтвердити VIP ($20)", callback_data=f"approve_vip_{user_id}"),
                        InlineKeyboardButton("🤖 Підтвердити Bot ($100)", callback_data=f"approve_bot_{user_id}")
                    ],
                    [InlineKeyboardButton("❌ Відхилити", callback_data=f"decline_{user_id}")]
                ])

                admin_text = f"📩 **НОВА КВИТАНЦІЯ!**\n\n👤 **Користувач:** @{username}\n🆔 **ID:** `{user_id}`\n🌐 **Мова:** {user_lang.upper()}"

                if update.message.photo:
                    photo_file_id = update.message.photo[-1].file_id
                    await bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=photo_file_id,
                        caption=admin_text,
                        reply_markup=admin_keyboard,
                        parse_mode="Markdown"
                    )
                    reply_msg = "✅ **Вашу квитанцію (фото) отримано!** Адміністратор перевірить її найближчим часом." if user_lang == "ua" else "✅ **Receipt received!** The admin will review it shortly."
                    await bot.send_message(chat_id=chat_id, text=reply_msg)
                    return {"status": "ok"}

                elif update.message.text:
                    full_admin_text = f"{admin_text}\n\n📝 **Текст / Хеш:**\n`{update.message.text}`"
                    await bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=full_admin_text,
                        reply_markup=admin_keyboard,
                        parse_mode="Markdown"
                    )
                    reply_msg = "✅ **Вашу квитанцію отримано!** Адміністратор перевірить її найближчим часом." if user_lang == "ua" else "✅ **Receipt received!** The admin will review it shortly."
                    await bot.send_message(chat_id=chat_id, text=reply_msg)
                    return {"status": "ok"}

        # 2. ОБРОБКА CALLBACK-КНОПОК
        elif update.callback_query:
            query = update.callback_query
            user_id = query.from_user.id
            username = query.from_user.username or "no_username"
            data = query.data
            user_lang = await get_user_lang(user_id)

            await query.answer()

            if data == "lang_ua":
                await set_user_lang(user_id, "ua")
                await query.edit_message_text(text=get_text_start("ua"), reply_markup=get_main_keyboard("ua"), parse_mode="Markdown")
            elif data == "lang_en":
                await set_user_lang(user_id, "en")
                await query.edit_message_text(text=get_text_start("en"), reply_markup=get_main_keyboard("en"), parse_mode="Markdown")

            elif data == "btn_back_main":
                await set_awaiting_support(user_id, 0)
                await query.edit_message_text(text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_support":
                await set_awaiting_support(user_id, 1)
                await query.edit_message_text(text=get_text_support_prompt(user_lang), reply_markup=get_cancel_support_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_cancel_support":
                await set_awaiting_support(user_id, 0)
                cancel_msg = "❌ Звернення в підтримку скасовано." if user_lang == "ua" else "❌ Support request cancelled."
                await query.edit_message_text(text=f"{cancel_msg}\n\n{get_text_start(user_lang)}", reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_services":
                await query.edit_message_text(text=get_text_services(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_rules":
                await query.edit_message_text(text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_buy_group":
                await query.edit_message_text(text=get_text_vip_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_connect_bot":
                await query.edit_message_text(text=get_text_bot_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_referral":
                me = await bot.get_me()
                ref_text = await get_referral_text(user_id, me.username, user_lang)
                await query.edit_message_text(text=ref_text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_free_trial":
                res_text = await handle_free_trial_request(user_id, username, user_lang)
                await query.edit_message_text(text=res_text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif data == "btn_my_sub":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT status, trial_end, sub_end, bot_sub_end, signal_token FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()

                if not row:
                    sub_info = "У вас немає активних підписок." if user_lang == "ua" else "You have no active subscriptions."
                else:
                    status, t_end, s_end, b_end, token = row
                    status_label = status.upper() if status else "FREE"
                    sub_info = (
                        f"📊 **Статус:** `{status_label}`\n\n"
                        if user_lang == "ua" else
                        f"📊 **Status:** `{status_label}`\n\n"
                    )

                    if t_end:
                        days_left = calc_days_left(t_end)
                        if user_lang == "ua":
                            sub_info += f"🎁 **Триал:** залишилось {days_left} дн. (до {t_end[:16].replace('T', ' ')} UTC)\n"
                        else:
                            sub_info += f"🎁 **Trial:** {days_left} days left (until {t_end[:16].replace('T', ' ')} UTC)\n"

                    if s_end:
                        days_left = calc_days_left(s_end)
                        if user_lang == "ua":
                            sub_info += f"💎 **VIP-група:** залишилось {days_left} дн. (до {s_end[:16].replace('T', ' ')} UTC)\n"
                        else:
                            sub_info += f"💎 **VIP Group:** {days_left} days left (until {s_end[:16].replace('T', ' ')} UTC)\n"

                    if b_end:
                        days_left = calc_days_left(b_end)
                        if user_lang == "ua":
                            sub_info += f"🤖 **Signal Bot:** залишилось {days_left} дн. (до {b_end[:16].replace('T', ' ')} UTC)\n"
                        else:
                            sub_info += f"🤖 **Signal Bot:** {days_left} days left (until {b_end[:16].replace('T', ' ')} UTC)\n"

                    if token:
                        sub_info += f"🔑 **OKX Token:** `{token[:6]}...{token[-4:]}`"

                await query.edit_message_text(text=sub_info, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # =======================================================
            # БЛОК ДОДАНОГО ФУНКЦІОНАЛУ: ОБРОБКА КНОПОК АДМІН-ПАНЕЛІ
            # =======================================================

            elif data.startswith("admin_"):
                if user_id != ADMIN_TELEGRAM_ID:
                    await query.answer("У вас немає доступу до цієї функції!", show_alert=True)
                    return {"status": "ok"}

                if data == "admin_users_list":
                    async with aiosqlite.connect(DB_PATH) as db:
                        async with db.execute("""
                            SELECT user_id, username, status, trial_end, sub_end, bot_sub_end 
                            FROM users 
                            ORDER BY user_id DESC 
                            LIMIT 30
                        """) as cursor:
                            users = await cursor.fetchall()
                    
                    if not users:
                        text = "📊 База користувачів порожня."
                    else:
                        text = "📊 *Останні користувачі та їх активні послуги:*\n\n"
                        now_utc = datetime.now(timezone.utc)
                        
                        for u_id, u_name, u_status, t_end, s_end, b_end in users:
                            u_name_disp = f"@{u_name}" if u_name and u_name != "no_username" else f"ID: `{u_id}`"
                            services = []
                            
                            if t_end:
                                try:
                                    t_dt = datetime.fromisoformat(t_end)
                                    if t_dt.tzinfo is None: t_dt = t_dt.replace(tzinfo=timezone.utc)
                                    if t_dt > now_utc:
                                        days_left = calc_days_left(t_end)
                                        services.append(f"⏳ Тріал: {days_left} дн. (до {t_dt.strftime('%d.%m')})")
                                except Exception: pass
                                    
                            if s_end:
                                try:
                                    s_dt = datetime.fromisoformat(s_end)
                                    if s_dt.tzinfo is None: s_dt = s_dt.replace(tzinfo=timezone.utc)
                                    if s_dt > now_utc:
                                        days_left = calc_days_left(s_end)
                                        services.append(f"💎 VIP: {days_left} дн. (до {s_dt.strftime('%d.%m')})")
                                except Exception: pass
                                    
                            if b_end:
                                try:
                                    b_dt = datetime.fromisoformat(b_end)
                                    if b_dt.tzinfo is None: b_dt = b_dt.replace(tzinfo=timezone.utc)
                                    if b_dt > now_utc:
                                        days_left = calc_days_left(b_end)
                                        services.append(f"🤖 Bot: {days_left} дн. (до {b_dt.strftime('%d.%m')})")
                                except Exception: pass
                                    
                            services_str = " | ".join(services) if services else "немає активних послуг"
                            status_disp = u_status.upper() if u_status else "FREE"
                            text += f"👤 {u_name_disp} — Статус: `{status_disp}`\n└ {services_str}\n\n"
                    
                    await query.edit_message_text(text=text, reply_markup=get_admin_panel_keyboard(), parse_mode="Markdown")
                
                elif data == "admin_grant_vip":
                    await query.edit_message_text(
                        text="Для надання VIP доступу, надішліть команду в чат у форматі:\n`/give_vip USER_ID`\n*(Наприклад: /give_vip 123456789)*", 
                        reply_markup=get_admin_panel_keyboard(), 
                        parse_mode="Markdown"
                    )
                    
                elif data == "admin_grant_bot":
                    await query.edit_message_text(
                        text="Для надання доступу до Signal Bot, надішліть команду в чат у форматі:\n`/give_bot USER_ID`\n*(Наприклад: /give_bot 123456789)*", 
                        reply_markup=get_admin_panel_keyboard(), 
                        parse_mode="Markdown"
                    )
            # =======================================================

            # АДМІНСЬКІ ДІЇ (ПІДТВЕРДЖЕННЯ / ВІДХИЛЕННЯ)
            elif data.startswith(("approve_vip_", "approve_bot_", "decline_")) and user_id == ADMIN_TELEGRAM_ID:
                action_type, target_user_id = data.rsplit("_", 1)
                target_user_id = int(target_user_id)
                target_lang = await get_user_lang(target_user_id)
                now = datetime.now(timezone.utc)
                new_end = now + timedelta(days=30)

                async with aiosqlite.connect(DB_PATH) as db:
                    if action_type == "approve_vip":
                        await db.execute("UPDATE users SET status = 'active', sub_end = ? WHERE user_id = ?", (new_end.isoformat(), target_user_id))
                        await db.commit()
                        
                        invite_link = await bot.create_chat_invite_link(chat_id=TELEGRAM_CHANNEL_ID, member_limit=1) if TELEGRAM_CHANNEL_ID else None
                        link_str = invite_link.invite_link if invite_link else "Перевірте канал."

                        user_msg = f"🎉 **Оплату VIP-групи підтверджено!**\n\n🔗 Ваше посилання для входу: {link_str}" if target_lang == "ua" else f"🎉 **VIP payment approved!**\n\n🔗 Link: {link_str}"
                        await bot.send_message(chat_id=target_user_id, text=user_msg)
                        await query.edit_message_text(text=f"✅ VIP підтверджено для ID: `{target_user_id}`")

                    elif action_type == "approve_bot":
                        await db.execute("UPDATE users SET bot_sub_end = ? WHERE user_id = ?", (new_end.isoformat(), target_user_id))
                        await db.commit()

                        await bot.send_message(chat_id=target_user_id, text=get_text_okx_instruction(target_lang), parse_mode="Markdown")
                        await query.edit_message_text(text=f"✅ Signal Bot підтверджено для ID: `{target_user_id}`")

                    elif action_type == "decline":
                        user_msg = "❌ **Вашу квитанцію відхилено.** Якщо ви виявили помилку, зверніться до підтримки." if target_lang == "ua" else "❌ **Receipt declined.** Please contact support if you believe this is an error."
                        await bot.send_message(chat_id=target_user_id, text=user_msg)
                        await query.edit_message_text(text=f"❌ Оплату відхилено для ID: `{target_user_id}`")

    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}")

    return {"status": "ok"}

# --- ЕНДПОІНТ ДЛЯ ПРИЙОМУ СИГНАЛІВ З TRADINGVIEW (WEBHOOK) ---

@app.post("/webhook")
async def tradingview_webhook(request: Request):
    try:
        data = await request.json()
        ticker = data.get("ticker", "UNKNOWN")
        action = data.get("action", "buy").lower()
        price = data.get("price", 0.0)

        now = datetime.now(timezone.utc)

        # 1. Запис у локальну БД
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO trades (ticker, action, price, timestamp) VALUES (?, ?, ?, ?)",
                             (ticker, action, price, now.isoformat()))
            await db.commit()

        # 2. Публікація сигналу в VIP Telegram-канал
        if TELEGRAM_CHANNEL_ID and bot:
            signal_text = f"🚨 **KERDOS SIGNAL** 🚨\n\n📊 **Монета:** #{ticker}\n🎯 **Дія:** {action.upper()}\n💵 **Ціна:** {price}\n⏰ **Час:** {now.strftime('%Y-%m-%d %H:%M UTC')}"
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=signal_text, parse_mode="Markdown")

        # 3. Трансляція сигналу активним користувачам OKX Signal Bot
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, username, signal_token FROM users WHERE signal_token IS NOT NULL AND bot_sub_end > ?",
                (now.isoformat(),)
            ) as cursor:
                bot_subscribers = await cursor.fetchall()

        if bot_subscribers:
            await send_signal_to_okx(bot_subscribers, ticker, action)

    except Exception as e:
        logger.error(f"Error processing TradingView webhook: {e}")
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}
