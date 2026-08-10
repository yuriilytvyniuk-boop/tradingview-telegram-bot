import os
import json
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Request
import aiosqlite
from aiogram import Bot, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- НАЛАШТУВАННЯ ЛОГУВАННЯ ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ОСНОВНІ ЗМІННІ СЕРЕДОВИЩА ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_PATH = os.getenv("DB_PATH", "database.db")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "123456789"))

app = FastAPI()
bot = Bot(token=BOT_TOKEN)


# --- АВТОМАТИЧНЕ СТВОРЕННЯ БАЗИ ДАНИХ ПРИ СТАРТІ ---
@app.on_event("startup")
async def on_startup():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                language TEXT DEFAULT 'ua',
                status TEXT,
                state TEXT,
                trial_end TEXT,
                sub_end TEXT,
                bot_sub_end TEXT,
                trial_used INTEGER DEFAULT 0,
                api_key TEXT,
                api_secret TEXT,
                passphrase TEXT
            )
        """)
        await db.commit()
    logger.info("База даних ініціалізована!")


# --- КЛАВІАТУРИ ---

def get_main_keyboard(lang="ua"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Моя підписка" if lang == "ua" else "⏳ My Subscription", callback_data="btn_my_sub")],
        [InlineKeyboardButton(text="🎁 Отримати 14 днів FREE" if lang == "ua" else "🎁 Claim 14 Days FREE", callback_data="btn_free_trial")],
        [InlineKeyboardButton(text="👥 Реферальна програма" if lang == "ua" else "👥 Referral Program", callback_data="btn_ref_program")],
        [InlineKeyboardButton(text="📊 Доступ до VIP-групи ($20 / 30 днів)" if lang == "ua" else "📊 VIP Group Access ($20 / 30 days)", callback_data="btn_buy_vip")],
        [InlineKeyboardButton(text="🤖 Підключити Signal Bot ($100 / 30 днів)" if lang == "ua" else "🤖 Connect Signal Bot ($100 / 30 days)", callback_data="btn_buy_bot")],
        [InlineKeyboardButton(text="💎 Послуги та ціни" if lang == "ua" else "💎 Services & Pricing", callback_data="btn_services")],
        [InlineKeyboardButton(text="📜 Правила спільноти" if lang == "ua" else "📜 Community Rules", callback_data="btn_rules")],
        [InlineKeyboardButton(text="💬 Чат спільноти" if lang == "ua" else "💬 Community Chat", url="https://t.me/your_community_chat")],
        [InlineKeyboardButton(text="🇬🇧 Switch to English" if lang == "ua" else "🇺🇦 Переключити на українську", callback_data="toggle_lang")]
    ])

def get_back_keyboard(lang="ua"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Повернутися в меню" if lang == "ua" else "🔙 Back to Menu", callback_data="btn_main_menu")]
    ])

async def get_user_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT language FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else "ua"


# --- TELEGRAM WEBHOOK ---

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = types.Update(**data)

        # 1. Обробка Inline-кнопок (Callback Queries)
        if update.callback_query:
            cb = update.callback_query
            user_id = cb.from_user.id
            chat_id = cb.message.chat.id
            cb_data = cb.data
            user_lang = await get_user_lang(user_id)
            now = datetime.now(timezone.utc)

            # Повернення в меню
            if cb_data == "btn_main_menu":
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET state = NULL WHERE user_id = ?", (user_id,))
                    await db.commit()
                menu_text = "🏠 **Головне меню**" if user_lang == "ua" else "🏠 **Main Menu**"
                await bot.send_message(chat_id=chat_id, text=menu_text, reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")

            # Перемикання мови
            elif cb_data == "toggle_lang":
                new_lang = "en" if user_lang == "ua" else "ua"
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET language = ? WHERE user_id = ?", (new_lang, user_id))
                    await db.commit()
                text = "Language updated!" if new_lang == "en" else "Мову оновлено!"
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_main_keyboard(new_lang))

            # Статус підписки
            elif cb_data == "btn_my_sub":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT status, trial_end, sub_end, bot_sub_end, trial_used FROM users WHERE user_id = ?", 
                        (user_id,)
                    ) as cursor:
                        row = await cursor.fetchone()

                if not row:
                    sub_info = (
                        "ℹ️ **У вас немає активних підписок.**\n\nВи можете активувати **14 днів FREE** у меню!"
                        if user_lang == "ua" else
                        "ℹ️ **You don't have any active subscriptions.**\n\nYou can claim your **14-day FREE trial** in the menu!"
                    )
                else:
                    status, trial_end, sub_end, bot_sub_end, trial_used = row
                    lines = []

                    def parse_dt(dt_str):
                        if not dt_str: return None
                        try:
                            dt = datetime.fromisoformat(dt_str)
                            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                        except ValueError: return None

                    dt_trial = parse_dt(trial_end)
                    dt_sub = parse_dt(sub_end)
                    dt_bot = parse_dt(bot_sub_end)
                    has_active = False

                    if status == 'trial' and dt_trial and dt_trial > now:
                        has_active = True
                        days_left = (dt_trial - now).days
                        hours_left = int((dt_trial - now).seconds / 3600)
                        lines.append(
                            f"🎁 **Тестовий період (VIP):** залишилося **{days_left} дн. {hours_left} год.**\n*(до {dt_trial.strftime('%Y-%m-%d %H:%M UTC')})*"
                            if user_lang == "ua" else
                            f"🎁 **Free Trial (VIP):** **{days_left}d {hours_left}h** remaining\n*(valid until {dt_trial.strftime('%Y-%m-%d %H:%M UTC')})*"
                        )
                    elif dt_sub and dt_sub > now:
                        has_active = True
                        days_left = (dt_sub - now).days
                        hours_left = int((dt_sub - now).seconds / 3600)
                        lines.append(
                            f"📊 **VIP-група Kerdos:** залишилося **{days_left} дн. {hours_left} год.**\n*(до {dt_sub.strftime('%Y-%m-%d %H:%M UTC')})*"
                            if user_lang == "ua" else
                            f"📊 **Kerdos VIP Group:** **{days_left}d {hours_left}h** remaining\n*(valid until {dt_sub.strftime('%Y-%m-%d %H:%M UTC')})*"
                        )

                    if dt_bot and dt_bot > now:
                        has_active = True
                        days_left = (dt_bot - now).days
                        hours_left = int((dt_bot - now).seconds / 3600)
                        lines.append(
                            f"🤖 **Signal Bot:** залишилося **{days_left} дн. {hours_left} год.**\n*(до {dt_bot.strftime('%Y-%m-%d %H:%M UTC')})*"
                            if user_lang == "ua" else
                            f"🤖 **Signal Bot:** **{days_left}d {hours_left}h** remaining\n*(valid until {dt_bot.strftime('%Y-%m-%d %H:%M UTC')})*"
                        )

                    if has_active:
                        header = "⏳ **Інформація про ваші підписки:**\n\n" if user_lang == "ua" else "⏳ **Your Subscription Status:**\n\n"
                        sub_info = header + "\n\n".join(lines)
                    else:
                        sub_info = (
                            "ℹ️ **У вас немає активних підписок.**\n\nВи можете активувати **14 днів FREE** або придбати доступ у меню."
                            if user_lang == "ua" else
                            "ℹ️ **You don't have any active subscriptions.**\n\nYou can claim your **14-day FREE trial** or buy a subscription in the main menu."
                        )

                await bot.send_message(chat_id=chat_id, text=sub_info, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # Активація Free Trial
            elif cb_data == "btn_free_trial":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT trial_used FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()
                        trial_used = row[0] if row else 0

                if trial_used == 1:
                    msg = (
                        "❌ **Ви вже використовували безкоштовний 14-денний період.**\n\nОберіть тарифний план у меню для продовження користування."
                        if user_lang == "ua" else
                        "❌ **You have already used your 14-day free trial.**\n\nPlease select a subscription plan from the menu."
                    )
                else:
                    trial_end_dt = (now + timedelta(days=14)).isoformat()
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute(
                            "UPDATE users SET status = 'trial', trial_end = ?, trial_used = 1 WHERE user_id = ?",
                            (trial_end_dt, user_id)
                        )
                        await db.commit()

                    msg = (
                        "🎉 **Вітаємо! Всі 14 днів FREE успішно активовано.**\n\nВам надано повний доступ до VIP-групи!"
                        if user_lang == "ua" else
                        "🎉 **Congratulations! Your 14-day FREE trial is activated.**\n\nYou now have full access to the VIP group!"
                    )

                await bot.send_message(chat_id=chat_id, text=msg, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # Купівля VIP / Bot
            elif cb_data in ["btn_buy_vip", "btn_buy_bot"]:
                sub_type = "vip" if cb_data == "btn_buy_vip" else "bot"
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET state = ? WHERE user_id = ?", (f"awaiting_receipt_{sub_type}", user_id))
                    await db.commit()

                amount = "$20" if sub_type == "vip" else "$100"
                pay_text = (
                    f"💳 **Оплата підписки ({amount} / 30 днів)**\n\n"
                    f"Будь ласка, перекажіть суму на реквізити та надішліть **скріншот квитанції** у цей чат.\n\n"
                    f"📍 **USDT TRC20:** `YOUR_TRC20_WALLET_HERE`"
                    if user_lang == "ua" else
                    f"💳 **Subscription Payment ({amount} / 30 days)**\n\n"
                    f"Please send the payment and attach a **screenshot of the receipt** in this chat.\n\n"
                    f"📍 **USDT TRC20:** `YOUR_TRC20_WALLET_HERE`"
                )
                await bot.send_message(chat_id=chat_id, text=pay_text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # Інші розділи
            elif cb_data == "btn_services":
                text = "💎 **Наші послуги:**\n\n• VIP-група з сигналами: **$20/міс**\n• Signal Bot для OKX: **$100/міс**" if user_lang == "ua" else "💎 **Our Services:**\n\n• VIP Signal Group: **$20/mo**\n• OKX Signal Bot: **$100/mo**"
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif cb_data == "btn_rules":
                text = "📜 **Правила спільноти:**\n\n1. Повага до учасників\n2. Заборона спаму та реклами" if user_lang == "ua" else "📜 **Community Rules:**\n\n1. Be respectful\n2. No spam or ads"
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            elif cb_data == "btn_ref_program":
                ref_link = f"https://t.me/your_bot?start={user_id}"
                text = f"👥 **Реферальна програма**\n\nВаше посилання:\n`{ref_link}`" if user_lang == "ua" else f"👥 **Referral Program**\n\nYour link:\n`{ref_link}`"
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=get_back_keyboard(user_lang), parse_mode="Markdown")

            # Підтвердження / Відхилення квитанції адміном
            elif cb_data.startswith("approve_vip_") or cb_data.startswith("approve_bot_"):
                target_user_id = int(cb_data.split("_")[2])
                is_vip = "vip" in cb_data
                end_dt = (now + timedelta(days=30)).isoformat()

                async with aiosqlite.connect(DB_PATH) as db:
                    if is_vip:
                        await db.execute("UPDATE users SET sub_end = ? WHERE user_id = ?", (end_dt, target_user_id))
                    else:
                        await db.execute("UPDATE users SET bot_sub_end = ? WHERE user_id = ?", (end_dt, target_user_id))
                    await db.commit()

                sub_name = "VIP-групу" if is_vip else "Signal Bot"
                await bot.send_message(chat_id=target_user_id, text=f"🎉 **Оплату підтверджено!** Доступ до {sub_name} активовано на 30 днів.")
                await bot.send_message(chat_id=chat_id, text=f"✅ Підписку для ID `{target_user_id}` успішно активовано.")

            elif cb_data.startswith("decline_"):
                target_user_id = int(cb_data.split("_")[1])
                await bot.send_message(chat_id=target_user_id, text="❌ **Вашу квитанцію відхилено.** Зв'яжіться з підтримкою у разі помилки.")
                await bot.send_message(chat_id=chat_id, text=f"❌ Оплату для ID `{target_user_id}` відхилено.")

            return {"status": "ok"}

        # 2. Обробка повідомлень та квитанцій
        if update.message:
            msg = update.message
            user_id = msg.from_user.id
            chat_id = msg.chat.id
            user_lang = await get_user_lang(user_id)

            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT state, username FROM users WHERE user_id = ?", (user_id,)) as cursor:
                    row = await cursor.fetchone()
                    user_state = row[0] if row else None
                    username = row[1] if row else "no_username"

            if msg.text and msg.text == "/start":
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO users (user_id, username, language) VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
                        (user_id, msg.from_user.username or "no_username", user_lang)
                    )
                    await db.commit()

                welcome_text = (
                    "👋 **Ласкаво просимо до Kerdos Bot!**\n\nОберіть потрібний розділ у меню нижче:"
                    if user_lang == "ua" else
                    "👋 **Welcome to Kerdos Bot!**\n\nPlease select an option from the menu below:"
                )
                await bot.send_message(chat_id=chat_id, text=welcome_text, reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")
                return {"status": "ok"}

            # Прийоми квитанцій (фото)
            if msg.photo and user_state in ['awaiting_receipt_vip', 'awaiting_receipt_bot']:
                sub_type = "vip" if user_state == 'awaiting_receipt_vip' else "bot"
                photo_id = msg.photo[-1].file_id

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET state = NULL WHERE user_id = ?", (user_id,))
                    await db.commit()

                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Прийняти", callback_data=f"approve_{sub_type}_{user_id}"),
                        InlineKeyboardButton(text="❌ Відхилити", callback_data=f"decline_{user_id}")
                    ]
                ])

                user_disp = f"@{username}" if username and username != "no_username" else f"ID: `{user_id}`"
                caption = f"🧾 **Нова квитанція на оплату ({sub_type.upper()})**\n\nКористувач: {user_disp}\nID: `{user_id}`"

                await bot.send_photo(chat_id=ADMIN_TELEGRAM_ID, photo=photo_id, caption=caption, reply_markup=admin_kb, parse_mode="Markdown")

                reply_msg = (
                    "✅ **Квитанцію відправлено на перевірку!** Адміністратор підтвердить доступ протягом декількох хвилин."
                    if user_lang == "ua" else
                    "✅ **Receipt sent for verification!** An administrator will verify your payment shortly."
                )
                await bot.send_message(chat_id=chat_id, text=reply_msg, reply_markup=get_main_keyboard(user_lang), parse_mode="Markdown")
                return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error in telegram_webhook: {e}")

    return {"status": "ok"}


# --- TRADINGVIEW WEBHOOK (ЗВІТ НАДСИЛАЄТЬСЯ АДМІНУ В ПРИВАТ) ---

@app.post("/webhook")
async def webhook(request: Request):
    try:
        raw_body = await request.body()
        data = json.loads(raw_body.decode("utf-8"))

        ticker = data.get("ticker", "BTCUSDT")
        action = data.get("action", "buy")
        okx_action = "BUY" if action.lower() == "buy" else "SELL"

        success_users = []
        failed_users = []

        now = datetime.now(timezone.utc)
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id, api_key, api_secret, passphrase, bot_sub_end FROM users WHERE api_key IS NOT NULL") as cursor:
                users = await cursor.fetchall()
                for u_id, api_key, api_secret, passphrase, bot_sub_end in users:
                    if bot_sub_end:
                        try:
                            sub_dt = datetime.fromisoformat(bot_sub_end)
                            if sub_dt.tzinfo is None:
                                sub_dt = sub_dt.replace(tzinfo=timezone.utc)
                            if sub_dt > now:
                                success_users.append(f"• User `{u_id}`")
                        except Exception:
                            failed_users.append(f"• User `{u_id}` (Error)")

        # Надсилання звіту безпосередньо адміну
        if ADMIN_TELEGRAM_ID and bot:
            report = f"🤖 **ЗВІТ РОЗСИЛКИ OKX SIGNAL BOT**\n\n"
            report += f"📊 **Сигнал:** {action.upper()} #{ticker}\n"
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
                logger.error(f"Не вдалося надіслати звіт адміну: {e}")

    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return {"status": "error", "message": str(e)}

    return {"status": "ok"}
