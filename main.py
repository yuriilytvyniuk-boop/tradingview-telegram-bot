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
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")  # ID VIP-групи
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))  # ID адміна

# 🔗 Посилання на загальну групу спілкування
PUBLIC_CHAT_LINK = os.getenv("PUBLIC_CHAT_LINK", "https://t.me/kerdos_group")

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
                lang TEXT DEFAULT 'ua'
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

# --- ФОНОВИЙ ТАЙМЕР ЗАВЕРШЕННЯ ТРИАЛУ ТА ПІДПИСКИ ---

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

                # 2. Завершення платній підписки
                async with db.execute(
                    "SELECT user_id, username, lang FROM users WHERE status = 'active' AND sub_end <= ?",
                    (now.isoformat(),)
                ) as cursor:
                    expired_subs = await cursor.fetchall()

                for user_id, username, lang in expired_subs:
                    try:
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
            [InlineKeyboardButton("🎁 Отримати 14 днів FREE", callback_data="btn_free_trial")],
            [InlineKeyboardButton("📊 Доступ до VIP-групи ($20 / 30 днів)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Підключити Signal Bot ($100 / 30 днів)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Послуги та ціни", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Правила спільноти", callback_data="btn_rules")],
            [InlineKeyboardButton("💬 Чат спільноти", url=PUBLIC_CHAT_LINK)],
            [InlineKeyboardButton("🇬🇧 Switch to English", callback_data="lang_en")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🎁 Get 14-Day Free Trial", callback_data="btn_free_trial")],
            [InlineKeyboardButton("📊 VIP Signals Group Access ($20 / 30 days)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Connect Signal Bot ($100 / 30 days)", callback_data="btn_connect_bot")],
            [InlineKeyboardButton("💎 Services & Pricing", callback_data="btn_services")],
            [InlineKeyboardButton("📜 Community Rules", callback_data="btn_rules")],
            [InlineKeyboardButton("💬 Community Chat", url=PUBLIC_CHAT_LINK)],
            [InlineKeyboardButton("🇺🇦 Переключити на Українську", callback_data="lang_ua")]
        ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(lang="ua"):
    back_text = "🔙 Повернутися в меню" if lang == "ua" else "🔙 Back to Menu"
    return InlineKeyboardMarkup([[InlineKeyboardButton(back_text, callback_data="btn_back_main")]])

# --- ТЕКСТИ ПОВІДОМЛЕНЬ ---

def get_text_start(lang="ua"):
    if lang == "ua":
        return (
            "👋 **Вітаємо у спільноті Kerdos!**\n\n"
            "Я — **Mireya**, ваш персональний помічник аналітичної торгової системи **Kerdos**.\n\n"
            "🎁 **Спеціальні пропозиції та Бонуси:**\n"
            "• 🚀 **14 днів FREE-доступу:** Кожен новий користувач отримує 2 тижні безкоштовного тестового доступу до VIP-групи Kerdos!\n"
            "• 👥 **Реферальна програма «Приведи друга»:** За кожного друга, який придбає підписку — отримуй **+14 днів безкоштовного доступу**!\n\n"
            "💎 **Наші Послуги та Прайс:**\n"
            "• 📊 **VIP-група з сигналами Kerdos:** **$20 / 30 днів** *(Аналітика ринку, торгові сигнали та чат спільноти)*\n"
            "• 🤖 **Персональний Signal Bot:** **$100 / 30 днів** *(Автоматичне підключення вашого акаунту OKX для миттєвої торгівлі)*\n\n"
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
        "• 👥 **\"Refer a Friend\" Program:** Bring a friend, and once they subscribe, get **+14 days of free VIP access**!\n\n"
        "💎 **Services & Pricing:**\n"
        "• 📊 **Kerdos VIP Signals Group:** **$20 / 30 days** *(Market analytics, trade signals, and community access)*\n"
        "• 🤖 **Personal Signal Bot Setup:** **$100 / 30 days** *(Direct OKX bot connection for automated signal execution)*\n\n"
        "📜 **Community Rules:**\n"
        "• 🚫 No spam, flooding, self-promotion, or referral links.\n"
        "• 🤝 Respectful communication, no profanity or toxicity.\n"
        "• 🛡️ Fraudulent behavior results in an immediate permanent ban.\n\n"
        "👇 **Choose an option from the menu below:**"
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
            "• **+14 днів** за кожного друга, який придбає підписку!"
        )
    return (
        "💎 **Services & Pricing (Kerdos)**\n\n"
        "📊 **VIP Signals Group Access:** **$20 / 30 days**\n\n"
        "🤖 **Personal Signal Bot Setup:** **$100 / 30 days**\n\n"
        "🎁 **Bonuses:**\n"
        "• **14-Day FREE Trial** for new users!\n"
        "• **+14 Days Free Access** for every referred friend who subscribes!"
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

# --- ЛОГІКА ТРИАЛУ ---

async def handle_free_trial_request(user_id: int, username: str, lang: str = "ua"):
    now = datetime.now(timezone.utc)
    trial_end = now + timedelta(days=14)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT trial_used FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()

        if user and user[0] == 1:
            if lang == "ua":
                return "⚠️ **Ви вже використовували безкоштовний 14-денний період.**\n\nВи можете оформити підписку у головному меню."
            return "⚠️ **You have already used your 14-day free trial.**\n\nYou can subscribe in the main menu."

        try:
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

# --- ВЕБХУК TELEGRAM ---

@app.post("/telegram_webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)

        if not update:
            return {"status": "ok"}

        if update.message:
            chat_id = update.message.chat_id
            user_id = update.message.from_user.id
            username = update.message.from_user.username or "no_username"
            user_lang = await get_user_lang(user_id)

            # Перевірка на надсилання Signal Token (наприклад: Token: abc123xyz)
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

            # Текстові команди
            if update.message.text:
                text = update.message.text.strip()
                if text in ["/start", "/services"]:
                    await bot.send_message(chat_id=chat_id, text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")
                    return {"status": "ok"}
                elif text == "/rules":
                    await bot.send_message(chat_id=chat_id, text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
                    return {"status": "ok"}

            # Прийом квитанції від користувача
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
                    await bot.send_photo(chat_id=ADMIN_TELEGRAM_ID, photo=update.message.photo[-1].file_id, caption=admin_text, reply_markup=admin_keyboard, parse_mode="Markdown")
                elif update.message.document:
                    await bot.send_document(chat_id=ADMIN_TELEGRAM_ID, document=update.message.document.file_id, caption=admin_text, reply_markup=admin_keyboard, parse_mode="Markdown")
                elif update.message.text:
                    admin_text += f"\n💬 **Текст:**\n{update.message.text}"
                    await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=admin_text, reply_markup=admin_keyboard, parse_mode="Markdown")

                confirm_reply = (
                    "✅ **Квитанцію отримано та передано адміністратору!**\nОчікуйте на підтвердження протягом дня."
                    if user_lang == "ua" else
                    "✅ **Receipt received and sent to the administrator!**\nPlease wait for verification."
                )
                await bot.send_message(chat_id=chat_id, text=confirm_reply, parse_mode="Markdown")

        # Callback кнопки
        elif update.callback_query:
            query = update.callback_query
            chat_id = query.message.chat_id
            message_id = query.message.message_id
            user_id = query.from_user.id
            username = query.from_user.username or "no_username"
            data = query.data

            await bot.answer_callback_query(callback_query_id=query.id)

            if data == "lang_ua":
                await set_user_lang(user_id, "ua")
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start("ua"), reply_markup=get_main_keyboard("ua"), parse_mode="Markdown")
            elif data == "lang_en":
                await set_user_lang(user_id, "en")
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start("en"), reply_markup=get_main_keyboard("en"), parse_mode="Markdown")
            elif data == "btn_back_main":
                user_lang = await get_user_lang(user_id)
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=get_text_start(user_lang), reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")
            elif data == "btn_services":
                user_lang = await get_user_lang(user_id)
                await bot.send_message(chat_id=chat_id, text=get_text_services(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
            elif data == "btn_rules":
                user_lang = await get_user_lang(user_id)
                await bot.send_message(chat_id=chat_id, text=get_text_rules(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
            elif data == "btn_free_trial":
                user_lang = await get_user_lang(user_id)
                response_text = await handle_free_trial_request(user_id, username, user_lang)
                await bot.send_message(chat_id=chat_id, text=response_text, parse_mode="Markdown")
            elif data == "btn_buy_group":
                user_lang = await get_user_lang(user_id)
                await bot.send_message(chat_id=chat_id, text=get_text_vip_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")
            elif data == "btn_connect_bot":
                user_lang = await get_user_lang(user_id)
                await bot.send_message(chat_id=chat_id, text=get_text_bot_payment(user_lang), reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # --- АДМІН СХВАЛЕННЯ З ДОДАВАННЯМ ЗАЛИШКУ ДНІВ ---
            elif data.startswith("approve_vip_"):
                target_user_id = int(data.split("_")[2])
                target_lang = await get_user_lang(target_user_id)
                now = datetime.now(timezone.utc)

                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT sub_end FROM users WHERE user_id = ?", (target_user_id,)) as cursor:
                        row = await cursor.fetchone()

                    current_sub_end = None
                    if row and row[0]:
                        try:
                            current_sub_end = datetime.fromisoformat(row[0])
                            if current_sub_end.tzinfo is None:
                                current_sub_end = current_sub_end.replace(tzinfo=timezone.utc)
                        except ValueError:
                            current_sub_end = None

                    # Якщо підписка ще діє, додаємо 30 днів до залишку; інакше від поточної дати
                    if current_sub_end and current_sub_end > now:
                        new_sub_end = current_sub_end + timedelta(days=30)
                    else:
                        new_sub_end = now + timedelta(days=30)

                    await db.execute("UPDATE users SET status = 'active', sub_end = ? WHERE user_id = ?", (new_sub_end.isoformat(), target_user_id))
                    await db.commit()

                invite_link = await bot.create_chat_invite_link(
                    chat_id=TELEGRAM_CHANNEL_ID,
                    member_limit=1,
                    expire_date=int((now + timedelta(hours=48)).timestamp())
                )

                approval_msg = (
                    f"🎉 **Вашу оплату VIP-групи Kerdos підтверджено!**\n\n"
                    f"⏰ Новий термін підписки активний до: **{new_sub_end.strftime('%Y-%m-%d %H:%M UTC')}**\n\n"
                    f"🔗 **Посилання для входу:**\n{invite_link.invite_link}"
                    if target_lang == "ua" else
                    f"🎉 **Your Kerdos VIP subscription has been approved!**\n\n"
                    f"⏰ New expiration date: **{new_sub_end.strftime('%Y-%m-%d %H:%M UTC')}**\n\n"
                    f"🔗 **Your invite link:**\n{invite_link.invite_link}"
                )
                await bot.send_message(chat_id=target_user_id, text=approval_msg, parse_mode="Markdown")

            elif data.startswith("approve_bot_"):
                target_user_id = int(data.split("_")[2])
                target_lang = await get_user_lang(target_user_id)
                now = datetime.now(timezone.utc)

                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT bot_sub_end FROM users WHERE user_id = ?", (target_user_id,)) as cursor:
                        row = await cursor.fetchone()

                    current_bot_sub_end = None
                    if row and row[0]:
                        try:
                            current_bot_sub_end = datetime.fromisoformat(row[0])
                            if current_bot_sub_end.tzinfo is None:
                                current_bot_sub_end = current_bot_sub_end.replace(tzinfo=timezone.utc)
                        except ValueError:
                            current_bot_sub_end = None

                    # Додаємо 30 днів до залишку або від поточної дати
                    if current_bot_sub_end and current_bot_sub_end > now:
                        new_bot_sub_end = current_bot_sub_end + timedelta(days=30)
                    else:
                        new_bot_sub_end = now + timedelta(days=30)

                    await db.execute("UPDATE users SET bot_sub_end = ? WHERE user_id = ?", (new_bot_sub_end.isoformat(), target_user_id))
                    await db.commit()

                await bot.send_message(chat_id=target_user_id, text=get_text_okx_instruction(target_lang), parse_mode="Markdown")

            elif data.startswith("decline_"):
                target_user_id = int(data.split("_")[1])
                target_lang = await get_user_lang(target_user_id)
                decline_msg = (
                    "❌ **На жаль, вашу квитанцію не було підтверджено.**"
                    if target_lang == "ua" else
                    "❌ **Unfortunately, your receipt could not be verified.**"
                )
                await bot.send_message(chat_id=target_user_id, text=decline_msg, parse_mode="Markdown")

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error handling Telegram webhook: {e}")
        return {"status": "error", "message": str(e)}

# --- TRADINGVIEW WEBHOOK (СИГНАЛИ ВІД KERDOS) ---

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
                f"🚀 **KERDOS SIGNAL: ENTRY LONG**\n\n"
                f"🪙 **Asset:** #{ticker}\n"
                f"💵 **Entry Price:** {price:.4f}\n"
                f"⏰ **Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}"
            )
        elif action in ["sell", "short"]:
            message_text = (
                f"🔻 **KERDOS SIGNAL: ENTRY SHORT**\n\n"
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
                f"🏁 **KERDOS SIGNAL: POSITION CLOSED**\n\n"
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
        report_text = "📊 **KERDOS MONTHLY PERFORMANCE REPORT**\n\nNo trades closed this month."
    else:
        rois = [r[0] for r in rows]
        total_trades = len(rois)
        winning_trades = sum(1 for r in rois if r > 0)
        win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0
        total_roi = sum(rois)
        
        roi_emoji = "🟢" if total_roi >= 0 else "🔴"
        
        report_text = (
            f"📊 **KERDOS MONTHLY PERFORMANCE REPORT**\n\n"
            f"🔢 **Total Trades:** {total_trades}\n"
            f"🎯 **Win Rate:** {win_rate:.1f}%\n"
            f"💰 **Total Net ROI:** {roi_emoji} **{total_roi:+.2f}%**\n\n"
            f"💡 *All signals generated automatically by Kerdos system.*"
        )
        
    if bot and TELEGRAM_CHANNEL_ID:
        await bot.send_message(
            chat_id=TELEGRAM_CHANNEL_ID,
            text=report_text,
            parse_mode="Markdown"
        )
        
    return {"status": "ok", "report": report_text}
