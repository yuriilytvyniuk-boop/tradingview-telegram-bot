import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
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
                            "⏳ <b>Ваш 14-денний тестовий період завершився!</b>\n\n"
                            "Сподіваємося, ви оцінили точність та якість сигналів <b>Kerdos</b>! 🚀\n\n"
                            "Щоб продовжити отримувати сигнали в реальному часі, оберіть варіант підписки нижче:"
                            if user_lang == "ua" else
                            "⏳ <b>Your 14-day free trial has expired!</b>\n\n"
                            "We hope you enjoyed the signal quality of <b>Kerdos</b>! 🚀\n\n"
                            "To keep receiving real-time signals, please select a subscription option below:"
                        )

                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=get_main_keyboard(user_lang),
                            parse_mode="HTML"
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
                            "⏳ <b>Термін вашої підписки на VIP-групу Kerdos закінчився.</b>\n\nДля продовження підписки скористайтеся меню бота."
                            if user_lang == "ua" else
                            "⏳ <b>Your Kerdos VIP group subscription has expired.</b>\n\nPlease use the menu to renew your access."
                        )

                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=get_main_keyboard(user_lang),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to remove expired sub user {user_id}: {e}")

        except Exception as e:
            logger.error(f"Error in check_expired_trials loop: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(check_expired_trials())
    yield

app = FastAPI(lifespan=lifespan)

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

# --- ТЕКСТИ ПОВІДОМЛЕНЬ ---

def get_text_start(lang="ua"):
    if lang == "ua":
        return (
            "👋 <b>Вітаємо у спільноті Kerdos!</b>\n\n"
            "Я — <b>Mireya</b>, ваш персональний помічник аналітичної торгової системи <b>Kerdos</b>.\n\n"
            "🎁 <b>Спеціальні пропозиції та Бонуси:</b>\n"
            "• 🚀 <b>14 днів FREE-доступу:</b> Кожен новий користувач отримує 2 тижні безкоштовного тестового доступу до VIP-групи Kerdos!\n"
            "• 👥 <b>Реферальна програма «Приведи друга»:</b> За кожного друга, який візьме безкоштовний пробний період — отримуй <b>+14 днів безкоштовного доступу</b>!\n\n"
            "💎 <b>Наші Послуги та Прайс:</b>\n"
            "• 📊 <b>VIP-група з сигналами Kerdos:</b> <b>$20 / 30 днів</b> <i>(Аналітика ринку, торгові сигнали та чат спільноти)</i>\n"
            "• 🤖 <b>Персональний Signal Bot:</b> <b>$100 / 30 днів</b> <i>(Автоматичне підключення вашого акаунту OKX для миттєвої торгівлі)</i>\n\n"
            "⚠️ <b>Управління ризиками та відповідальність:</b>\n"
            "• 📈 Торгівля на криптовалютному ринку завжди пов'язана з високими ризиками.\n"
            "• 🛡️ Обов'язково дотримуйтесь суворого <b>ризик- та мані-менеджменту</b> — контролюйте розмір плеча та закладайте безпечний відсоток депозиту на одну угоду.\n"
            "• ⚖️ Ми <b>не несемо відповідальності</b> за ваш баланс та фінансові результати — ви повністю контролюєте власні кошти та самостійно приймаєте рішення.\n"
            "• 🔥 Проте при дотриманні дисципліни, системного підходу та правил стратегії — це дає чудові результати!\n\n"
            "📜 <b>Правила спільноти:</b>\n"
            "• 🚫 Без спаму, флуду, реклами та реферальних посилань.\n"
            "• 🤝 Ввічливе спілкування, без мату та токсичності.\n"
            "• 🛡️ Шахрайство = негайний бан.\n\n"
            "👇 <b>Обери потрібну дію з меню нижче:</b>"
        )
    return (
        "👋 <b>Welcome to the Kerdos community!</b>\n\n"
        "I am <b>Mireya</b>, your personal assistant for the <b>Kerdos</b> trading system.\n\n"
        "🎁 <b>Special Offers & Bonuses:</b>\n"
        "• 🚀 <b>14-Day FREE Trial:</b> Every new user gets 2 weeks of free trial access to our Kerdos VIP Signals Group!\n"
        "• 👥 <b>\"Refer a Friend\" Program:</b> Bring a friend, and once they claim their free trial, get <b>+14 days of free VIP access</b>!\n\n"
        "💎 <b>Services & Pricing:</b>\n"
        "• 📊 <b>Kerdos VIP Signals Group:</b> <b>$20 / 30 days</b> <i>(Market analytics, trade signals, and community access)</i>\n"
        "• 🤖 <b>Personal Signal Bot Setup:</b> <b>$100 / 30 days</b> <i>(Direct OKX bot connection for automated signal execution)</i>\n\n"
        "⚠️ <b>Risk Management & Disclaimer:</b>\n"
        "• 📈 Cryptocurrency trading involves substantial financial risk.\n"
        "• 🛡️ Always practice strict <b>risk and money management</b> — control your leverage and allocate a safe percentage of your capital per trade.\n"
        "• ⚖️ We <b>are not responsible</b> for your balance or trading outcomes — you maintain full control over your funds and make decisions independently.\n"
        "• 🔥 However, with proper discipline and strategic rule execution, it yields excellent long-term results!\n\n"
        "👇 <b>Choose an option from the menu below:</b>"
    )

def get_text_support_prompt(lang="ua"):
    if lang == "ua":
        return (
            "🛟 <b>СЛУЖБА ПІДТРИМКИ KERDOS</b>\n\n"
            "Ви виявили помилку, маєте запитання щодо підписки або потребуєте допомоги з налаштуванням?\n\n"
            "📝 <b>Будь ласка, опишіть вашу проблему нижче в одному повідомленні:</b>\n"
            "<i>(Ви також можете додати скріншот або фото помилки)</i>\n\n"
            "⏳ <i>Mireya одразу ж передасть ваше звернення адміністратору!</i>"
        )
    return (
        "🛟 <b>KERDOS SUPPORT HELPDESK</b>\n\n"
        "Did you encounter an issue, have questions about your subscription, or need setup assistance?\n\n"
        "📝 <b>Please describe your issue below in a single message:</b>\n"
        "<i>(You can also attach a screenshot or photo)</i>\n\n"
        "⏳ <i>Mireya will forward your ticket directly to the administrator!</i>"
    )

def get_text_vip_payment(lang="ua"):
    if lang == "ua":
        return (
            "💳 <b>Оплата підписки на VIP-групу Kerdos ($20 / 30 днів)</b>\n\n"
            "Для активації підписки перекажіть <b>20 USDT</b> на один із гаманців Binance нижче:\n\n"
            f"🔸 <b>USDT (TRC20):</b>\n<code>{WALLET_USDT_TRC20}</code>\n\n"
            f"🔹 <b>USDT (BEP20 / BNB Chain):</b>\n<code>{WALLET_USDT_BEP20}</code>\n\n"
            f"🟣 <b>USDT (Solana):</b>\n<code>{WALLET_USDT_SOLANA}</code>\n\n"
            "<i>(Натисніть на адресу, щоб її скопіювати)</i>\n\n"
            "📥 <b>ПІДТВЕРДЖЕННЯ ОПЛАТИ:</b>\n"
            "Після виконання переказу <b>надішліть квитанцію (фото, скріншот або текст з хешем транзакції) сюди в чат</b>.\n\n"
            "Я (Mireya) передам її адміністратору на перевірку, і доступ буде надано!"
        )
    return (
        "💳 <b>Kerdos VIP Group Subscription ($20 / 30 days)</b>\n\n"
        "To activate your subscription, send <b>20 USDT</b> to one of the Binance wallets below:\n\n"
        f"🔸 <b>USDT (TRC20):</b>\n<code>{WALLET_USDT_TRC20}</code>\n\n"
        f"🔹 <b>USDT (BEP20 / BNB Chain):</b>\n<code>{WALLET_USDT_BEP20}</code>\n\n"
        f"🟣 <b>USDT (Solana):</b>\n<code>{WALLET_USDT_SOLANA}</code>\n\n"
        "<i>(Tap the address to copy it)</i>\n\n"
        "📥 <b>HOW TO CONFIRM PAYMENT:</b>\n"
        "After completing the transfer, <b>send the receipt (photo, screenshot, or transaction TxID) directly into this chat</b>.\n\n"
        "I (Mireya) will forward it to the admin for verification!"
    )

def get_text_bot_payment(lang="ua"):
    if lang == "ua":
        return (
            "🤖 <b>Підключення Kerdos Signal Bot ($100 / 30 днів)</b>\n\n"
            "Персональний бот для автоматичного виконання сигналів <b>Kerdos</b> на вашому акаунті OKX.\n\n"
            "⚡ <b>Переваги:</b>\n"
            "• Автоматичне відкриття/закриття угод 24/7\n"
            "• Без передачі API-ключів (безпечно через Signal Token)\n"
            "• Миттєва швидкість виконання сигналів\n\n"
            "💳 <b>Вартість:</b> <b>$100 / 30 днів</b>\n\n"
            "Перекажіть <b>100 USDT</b> на один із гаманців Binance:\n\n"
            f"🔸 <b>USDT (TRC20):</b>\n<code>{WALLET_USDT_TRC20}</code>\n\n"
            f"🔹 <b>USDT (BEP20 / BNB Chain):</b>\n<code>{WALLET_USDT_BEP20}</code>\n\n"
            f"🟣 <b>USDT (Solana):</b>\n<code>{WALLET_USDT_SOLANA}</code>\n\n"
            "📥 <b>ПІДТВЕРДЖЕННЯ ОПЛАТИ:</b>\n"
            "Після переказу <b>надішліть квитанцію (скріншот або хеш) сюди в чат</b>."
        )
    return (
        "🤖 <b>Connect Kerdos Signal Bot ($100 / 30 days)</b>\n\n"
        "Automated bot for executing <b>Kerdos</b> signals directly on your OKX account.\n\n"
        "⚡ <b>Benefits:</b>\n"
        "• 24/7 automated trade execution\n"
        "• Safe setup without sharing API keys (via Signal Token)\n"
        "• Instant signal execution speed\n\n"
        "💳 <b>Price:</b> <b>$100 / 30 days</b>\n\n"
        "Send <b>100 USDT</b> to one of the Binance wallets below:\n\n"
        f"🔸 <b>USDT (TRC20):</b>\n<code>{WALLET_USDT_TRC20}</code>\n\n"
        f"🔹 <b>USDT (BEP20 / BNB Chain):</b>\n<code>{WALLET_USDT_BEP20}</code>\n\n"
        f"🟣 <b>USDT (Solana):</b>\n<code>{WALLET_USDT_SOLANA}</code>\n\n"
        "📥 <b>HOW TO CONFIRM PAYMENT:</b>\n"
        "After transferring, <b>send your receipt (photo, screenshot, or TxID) into this chat</b>."
    )

def get_text_services(lang="ua"):
    if lang == "ua":
        return (
            "💎 <b>Наші Послуги та Прайс (Kerdos)</b>\n\n"
            "📊 <b>VIP-група з сигналами:</b> <b>$20 / 30 днів</b>\n\n"
            "🤖 <b>Персональний Signal Bot:</b> <b>$100 / 30 днів</b>\n\n"
            "🎁 <b>Бонуси:</b>\n"
            "• <b>14 днів FREE</b> для нових користувачів!\n"
            "• <b>+14 днів</b> за кожного друга, який візьме безкоштовний пробний період!"
        )
    return (
        "💎 <b>Services & Pricing (Kerdos)</b>\n\n"
        "📊 <b>VIP Signals Group Access:</b> <b>$20 / 30 days</b>\n\n"
        "🤖 <b>Personal Signal Bot Setup:</b> <b>$100 / 30 days</b>\n\n"
        "🎁 <b>Bonuses:</b>\n"
        "• <b>14-Day FREE Trial</b> for new users!\n"
        "• <b>+14 Days Free Access</b> for every referred friend who claims their free trial!"
    )

def get_text_rules(lang="ua"):
    if lang == "ua":
        return (
            "📜 <b>Правила спільноти Kerdos</b>\n\n"
            "🚫 <b>Без спаму та флуду:</b> Масові розсилки заборонені.\n"
            "❌ <b>Заборона реклами:</b> Реклама без дозволу заборонена.\n"
            "🤝 <b>Повага та етика:</b> Образи та токсичність неприпустимі.\n"
            "🤬 <b>Без нецензурної лексики:</b> Дотримуємося ввічливого спілкування.\n"
            "🛡️ <b>Без шахрайства:</b> Спроби скаму = бан."
        )
    return (
        "📜 <b>Kerdos Community Rules</b>\n\n"
        "🚫 <b>No Spam or Flooding:</b> Mass messaging is prohibited.\n"
        "❌ <b>No Advertising:</b> Self-promotion is forbidden.\n"
        "🤝 <b>Respect & Courtesy:</b> Toxicity will not be tolerated.\n"
        "🤬 <b>No Profanity:</b> Keep communication polite and clean.\n"
        "🛡️ <b>No Scams:</b> Immediate permanent ban."
    )

def get_text_okx_instruction(lang="ua"):
    if lang == "ua":
        return (
            "🎉 <b>Оплату Kerdos Signal Bot підтверджено!</b>\n\n"
            "Для підключення вашого акаунту OKX до системи сигналів <b>Kerdos</b>, будь ласка, надайте ваш <b>Signal Token</b>.\n\n"
            "📍 <b>Де знайти Signal Token на OKX:</b>\n"
            "1. Зайдіть на біржу <b>OKX</b> ➔ розділ <b>Торгувати (Trade)</b> ➔ <b>Торгові боти (Trading Bots)</b>.\n"
            "2. Оберіть <b>Сигнальний бот (Signal Bot)</b> ➔ <b>Створити власні сигнали (Create Custom Signal)</b>.\n"
            "3. Введіть назву сигналу (наприклад, <code>Kerdos Signals</code>) та натисніть <b>Створити</b>.\n"
            "4. Скопіюйте рядок <b>Signal Token</b> з налаштувань бота.\n\n"
            "📥 <b>Надішліть ваш токен у цей чат у такому форматі:</b>\n"
            "<code>Token: ваш_signal_token_тут</code>"
        )
    return (
        "🎉 <b>Kerdos Signal Bot payment approved!</b>\n\n"
        "To connect your OKX account to the <b>Kerdos</b> signal system, please provide your <b>Signal Token</b>.\n\n"
        "📍 <b>Where to find Signal Token on OKX:</b>\n"
        "1. Go to <b>OKX</b> ➔ <b>Trade</b> ➔ <b>Trading Bots</b>.\n"
        "2. Select <b>Signal Bot</b> ➔ <b>Create Custom Signal</b>.\n"
        "3. Name your signal (e.g., <code>Kerdos Signals</code>) and click <b>Create</b>.\n"
        "4. Copy the <b>Signal Token</b> string from the bot settings.\n\n"
        "📥 <b>Send your token in this chat using the format:</b>\n"
        "<code>Token: your_signal_token_here</code>"
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
            "👥 <b>Реферальна програма Kerdos «Приведи друга»</b>\n\n"
            "Запрошуйте друзів та отримуйте <b>+14 днів безкоштовного доступу</b> до VIP-групи за кожного друга, який активує безкоштовний пробний період!\n\n"
            f"🔗 <b>Ваше персональне посилання:</b>\n<code>{ref_link}</code>\n\n"
            f"📊 <b>Ваші запрошені друзі, які взяли FREE-триал:</b> {active_refs}\n\n"
            "<i>(Натисніть на посилання, щоб скопіювати його та поділитися з друзями)</i>"
        )
    return (
        "👥 <b>Kerdos Referral Program \"Refer a Friend\"</b>\n\n"
        "Invite your friends and receive <b>+14 days of free VIP access</b> for every friend who activates their free trial!\n\n"
        f"🔗 <b>Your personal referral link:</b>\n<code>{ref_link}</code>\n\n"
        f"📊 <b>Friends who claimed FREE trial:</b> {active_refs}\n\n"
        "<i>(Tap the link to copy and share it with your friends)</i>"
    )

async def handle_free_trial_request(user_id: int, username: str, lang: str = "ua"):
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=14)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT trial_used, referrer_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if user and user[0] == 1:
            if lang == "ua":
                return "⚠️ <b>Ви вже використовували безкоштовний 14-денний період.</b>\n\nВи можете оформити підписку у головному меню."
            return "⚠️ <b>You have already used your 14-day free trial.</b>\n\nYou can subscribe in the main menu."

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
                        f"🥳 <b>Ваш друг (@{username}) взяв безкоштовний тестовий період!</b>\n\n"
                        f"🎁 Вам автоматично нараховано <b>+14 днів безкоштовного доступу</b> до Kerdos VIP!\n"
                        f"⏰ Новий термін дії доступу: <b>{new_end.strftime('%Y-%m-%d %H:%M UTC')}</b>"
                        if ref_lang == "ua" else
                        f"🥳 <b>Your friend (@{username}) claimed their free trial!</b>\n\n"
                        f"🎁 You have automatically received <b>+14 free days</b> of Kerdos VIP access!\n"
                        f"⏰ New expiration date: <b>{new_end.strftime('%Y-%m-%d %H:%M UTC')}</b>"
                    )
                    try:
                        await bot.send_message(chat_id=referrer_id, text=bonus_msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

            if lang == "ua":
                return (
                    f"🎉 <b>Вам надано 14 днів безкоштовного доступу до Kerdos VIP!</b>\n\n"
                    f"🔗 <b>Ваше одноразове посилання:</b>\n{invite_link.invite_link}\n\n"
                    f"⏰ Доступ активний до: <b>{trial_end.strftime('%Y-%m-%d %H:%M UTC')}</b>"
                )
            return (
                f"🎉 <b>You have been granted 14 days of free access to Kerdos VIP!</b>\n\n"
                f"🔗 <b>Your invite link:</b>\n{invite_link.invite_link}\n\n"
                f"⏰ Access valid until: <b>{trial_end.strftime('%Y-%m-%d %H:%M UTC')}</b>"
            )
        except Exception as e:
            logger.error(f"Error creating invite link for user {user_id}: {e}")
            return "❌ Помилка при створенні посилання. Переконайся, що Mireya додана у групу як адмін."

# --- РОЗСИЛКА СИГНАЛІВ НА OKX SIGNAL BOT ТА ЗВІТ АДМІНУ ---

async def send_signal_to_okx(tokens_info: list[tuple], ticker: str, okx_action: str):
    if not tokens_info or not okx_action:
        logger.info("Немає активних підписників Signal Bot або некоректна дія.")
        return

    # Очищення тикера для OKX
    formatted_ticker = ticker.split(".")[0].replace("USDT", "").replace("-", "").replace("_", "").upper()
    instrument = f"{formatted_ticker}-USDT-SWAP"

    success_users = []
    failed_users = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for user_id, username, token in tokens_info:
            user_disp = f"@{username}" if username and username != "no_username" else f"ID: {user_id}"
            payload = {
                "signalToken": token,
                "action": okx_action,
                "instrument": instrument
            }
            try:
                response = await client.post(OKX_SIGNAL_WEBHOOK_URL, json=payload)
                if response.status_code == 200:
                    logger.info(f"✅ Сигнал відправлено на OKX для {user_disp}")
                    success_users.append(f"• {user_disp} (<code>{user_id}</code>)")
                else:
                    logger.error(f"❌ Помилка OKX [{response.status_code}] для {user_disp}: {response.text}")
                    failed_users.append(f"• {user_disp} (<code>{user_id}</code>) — Код: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Збій відправки на OKX для {user_disp}: {e}")
                failed_users.append(f"• {user_disp} (<code>{user_id}</code>) — {e}")

    if ADMIN_TELEGRAM_ID and bot:
        report = f"🤖 <b>ЗВІТ РОЗСИЛКИ OKX SIGNAL BOT</b>\n\n"
        report += f"📊 <b>Монета:</b> #{formatted_ticker}USDT\n"
        report += f"🎯 <b>Дія OKX:</b> <code>{okx_action}</code>\n\n"
        report += f"✅ <b>Успішно виконано ({len(success_users)}):</b>\n"
        report += ("\n".join(success_users) if success_users else "Немає") + "\n\n"
        
        if failed_users:
            report += f"❌ <b>Помилки ({len(failed_users)}):</b>\n"
            report += "\n".join(failed_users)

        try:
            await bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=report,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Не вдалося надіслати звіт адміну: {e}")

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
                    "✅ <b>Signal Token успішно збережено!</b>\n\nВаш акаунт OKX прив'язано до системи сигналів <b>Kerdos</b>."
                    if user_lang == "ua" else
                    "✅ <b>Signal Token saved successfully!</b>\n\nYour OKX account is now connected to <b>Kerdos</b> signals."
                )
                await bot.send_message(chat_id=chat_id, text=success_text, parse_mode="HTML")
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

                    await bot.send_message(chat_id=chat_id, text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="HTML")
                    return {"status": "ok"}

                elif text == "/services":
                    await bot.send_message(chat_id=chat_id, text=get_text_services(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")
                    return {"status": "ok"}
                elif text == "/rules":
                    await bot.send_message(chat_id=chat_id, text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")
                    return {"status": "ok"}

            # 📩 ОБРОБКА ЗВЕРНЕННЯ В ПІДТРИМКУ
            if is_awaiting_support == 1 and ADMIN_TELEGRAM_ID and user_id != ADMIN_TELEGRAM_ID:
                await set_awaiting_support(user_id, 0)
                
                admin_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Ввійти в чат / Відповісти", url=f"tg://user?id={user_id}")]
                ])

                support_header = f"🛟 <b>НОВЕ ЗВЕРНЕННЯ В ПІДТРИМКУ!</b>\n\n👤 <b>Від:</b> @{username}\n🆔 <b>ID:</b> <code>{user_id}</code>\n🌐 <b>Мова:</b> {user_lang.upper()}\n"

                if update.message.photo:
                    photo_file_id = update.message.photo[-1].file_id
                    caption_text = f"{support_header}\n📝 <b>Опис:</b>\n{update.message.caption or 'Без опису'}"
                    await bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=photo_file_id,
                        caption=caption_text,
                        reply_markup=admin_keyboard,
                        parse_mode="HTML"
                    )
                elif update.message.text:
                    full_support_text = f"{support_header}\n📝 <b>Опис помилки:</b>\n{update.message.text}"
                    await bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=full_support_text,
                        reply_markup=admin_keyboard,
                        parse_mode="HTML"
                    )

                confirm_text = (
                    "🚀 <b>Ваше звернення успішно передано адміністратору!</b>\n\nМи розглянемо його найближчим часом та зв'яжемося з вами."
                    if user_lang == "ua" else
                    "🚀 <b>Your support request has been delivered to the admin!</b>\n\nWe will review it and get back to you shortly."
                )
                await bot.send_message(chat_id=chat_id, text=confirm_text, reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")
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

                admin_text = f"📩 <b>НОВА КВИТАНЦІЯ!</b>\n\n👤 <b>Користувач:</b> @{username}\n🆔 <b>ID:</b> <code>{user_id}</code>\n🌐 <b>Мова:</b> {user_lang.upper()}"

                if update.message.photo:
                    photo_file_id = update.message.photo[-1].file_id
                    await bot.send_photo(
                        chat_id=ADMIN_TELEGRAM_ID,
                        photo=photo_file_id,
                        caption=admin_text,
                        reply_markup=admin_keyboard,
                        parse_mode="HTML"
                    )
                    reply_msg = "✅ <b>Вашу квитанцію (фото) отримано!</b> Адміністратор перевірить її найближчим часом." if user_lang == "ua" else "✅ <b>Receipt received!</b> The admin will review it shortly."
                    await bot.send_message(chat_id=chat_id, text=reply_msg, parse_mode="HTML")
                    return {"status": "ok"}

                elif update.message.text:
                    full_admin_text = f"{admin_text}\n\n📝 <b>Текст / Хеш:</b>\n<code>{update.message.text}</code>"
                    await bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=full_admin_text,
                        reply_markup=admin_keyboard,
                        parse_mode="HTML"
                    )
                    reply_msg = "✅ <b>Вашу квитанцію отримано!</b> Адміністратор перевірить її найближчим часом." if user_lang == "ua" else "✅ <b>Receipt received!</b> The admin will review it shortly."
                    await bot.send_message(chat_id=chat_id, text=reply_msg, parse_mode="HTML")
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
                await query.edit_message_text(text=get_text_start("ua"), reply_markup=get_main_keyboard("ua"), parse_mode="HTML")
            elif data == "lang_en":
                await set_user_lang(user_id, "en")
                await query.edit_message_text(text=get_text_start("en"), reply_markup=get_main_keyboard("en"), parse_mode="HTML")

            elif data == "btn_back_main":
                await set_awaiting_support(user_id, 0)
                await query.edit_message_text(text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_support":
                await set_awaiting_support(user_id, 1)
                await query.edit_message_text(text=get_text_support_prompt(user_lang), reply_markup=get_cancel_support_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_cancel_support":
                await set_awaiting_support(user_id, 0)
                cancel_msg = "❌ Звернення в підтримку скасовано." if user_lang == "ua" else "❌ Support request cancelled."
                await query.edit_message_text(text=f"{cancel_msg}\n\n{get_text_start(user_lang)}", reply_markup=get_main_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_services":
                await query.edit_message_text(text=get_text_services(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_rules":
                await query.edit_message_text(text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_buy_group":
                await query.edit_message_text(text=get_text_vip_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_connect_bot":
                await query.edit_message_text(text=get_text_bot_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_referral":
                me = await bot.get_me()
                ref_text = await get_referral_text(user_id, me.username, user_lang)
                await query.edit_message_text(text=ref_text, reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_free_trial":
                res_text = await handle_free_trial_request(user_id, username, user_lang)
                await query.edit_message_text(text=res_text, reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

            elif data == "btn_my_sub":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT status, trial_end, sub_end, bot_sub_end, signal_token FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()

                if not row:
                    sub_info = "У вас немає активних підписок." if user_lang == "ua" else "You have no active subscriptions."
                else:
                    status, t_end, s_end, b_end, token = row
                    sub_info = f"📊 <b>Статус:</b> <code>{status.upper()}</code>\n\n"
                    if t_end: sub_info += f"🎁 <b>Триал до:</b> {t_end[:16].replace('T', ' ')} UTC\n"
                    if s_end: sub_info += f"💎 <b>VIP-група до:</b> {s_end[:16].replace('T', ' ')} UTC\n"
                    if b_end: sub_info += f"🤖 <b>Signal Bot до:</b> {b_end[:16].replace('T', ' ')} UTC\n"
                    if token: sub_info += f"🔑 <b>OKX Token:</b> <code>{token[:6]}...{token[-4:]}</code>"

                await query.edit_message_text(text=sub_info, reply_markup=get_back_keyboard(user_lang), parse_mode="HTML")

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

                        user_msg = f"🎉 <b>Оплату VIP-групи підтверджено!</b>\n\n🔗 Ваше посилання для входу: {link_str}" if target_lang == "ua" else f"🎉 <b>VIP payment approved!</b>\n\n🔗 Link: {link_str}"
                        await bot.send_message(chat_id=target_user_id, text=user_msg, parse_mode="HTML")
                        await query.edit_message_text(text=f"✅ VIP підтверджено для ID: <code>{target_user_id}</code>", parse_mode="HTML")

                    elif action_type == "approve_bot":
                        await db.execute("UPDATE users SET bot_sub_end = ? WHERE user_id = ?", (new_end.isoformat(), target_user_id))
                        await db.commit()

                        await bot.send_message(chat_id=target_user_id, text=get_text_okx_instruction(target_lang), parse_mode="HTML")
                        await query.edit_message_text(text=f"✅ Signal Bot підтверджено для ID: <code>{target_user_id}</code>", parse_mode="HTML")

                    elif action_type == "decline":
                        user_msg = "❌ <b>Вашу квитанцію відхилено.</b> Якщо ви виявили помилку, зверніться до підтримки." if target_lang == "ua" else "❌ <b>Receipt declined.</b> Please contact support if you believe this is an error."
                        await bot.send_message(chat_id=target_user_id, text=user_msg, parse_mode="HTML")
                        await query.edit_message_text(text=f"❌ Оплату відхилено для ID: <code>{target_user_id}</code>", parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}")

    return {"status": "ok"}

# --- ЕНДПОІНТ ДЛЯ ПРИЙОМУ СИГНАЛІВ З TRADINGVIEW (WEBHOOK) ---

@app.post("/webhook")
@app.post("/tradingview_webhook")
async def tradingview_webhook(request: Request):
    try:
        data = await request.json()
        raw_ticker = str(data.get("ticker", "UNKNOWN"))
        raw_action = str(data.get("action", "buy")).lower()
        price = data.get("price", 0.0)
        market_position = str(data.get("market_position", "")).lower()
        position_size = float(data.get("position_size", 0.0))

        # 🧹 Очищення тикера від суфіксів (.P, .PERP, -SWAP тощо)
        # Приклад: "BTCUSDT.P" -> "BTCUSDT"
        clean_ticker = raw_ticker.split(".")[0].replace("-", "").replace("_", "").upper()

        now = datetime.now(timezone.utc)

        # Логіка визначення дії для OKX
        okx_action = None
        if market_position == "flat" or position_size == 0:
            if raw_action in ["sell", "close"]:
                okx_action = "exit_long"
            elif raw_action in ["buy", "close"]:
                okx_action = "exit_short"
        elif market_position == "long" or raw_action in ["buy", "long"]:
            okx_action = "enter_long"
        elif market_position == "short" or raw_action in ["sell", "short"]:
            okx_action = "enter_short"

        # 1. Запис у локальну БД (збереження вже очищеного тикера)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO trades (ticker, action, price, timestamp) VALUES (?, ?, ?, ?)",
                (clean_ticker, raw_action, price, now.isoformat())
            )
            await db.commit()

        # 2. Публікація сигналу в VIP Telegram-канал (у форматі #BTCUSDT)
        if TELEGRAM_CHANNEL_ID and bot:
            signal_text = (
                f"🚨 <b>KERDOS SIGNAL</b> 🚨\n\n"
                f"📊 <b>Монета:</b> #{clean_ticker}\n"
                f"🎯 <b>Дія:</b> {raw_action.upper()}\n"
                f"💵 <b>Ціна:</b> {price}\n"
                f"⏰ <b>Час:</b> {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
            await bot.send_message(chat_id=TELEGRAM_CHANNEL_ID, text=signal_text, parse_mode="HTML")

        # 3. Трансляція сигналу активним користувачам OKX Signal Bot
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, username, signal_token FROM users WHERE signal_token IS NOT NULL AND bot_sub_end > ?",
                (now.isoformat(),)
            ) as cursor:
                bot_subscribers = await cursor.fetchall()

        if bot_subscribers and okx_action:
            await send_signal_to_okx(bot_subscribers, clean_ticker, okx_action)

    except Exception as e:
        logger.error(f"Error processing TradingView webhook: {e}")
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}
