import os
import re
import logging
import asyncio
import aiosqlite
import httpx
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton, Update

# --- НАЛАШТУВАННЯ ЛОГУВАННЯ ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- ЗМІННІ ОТОЧЕННЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VIP_CHANNEL_ID = os.getenv("VIP_CHANNEL_ID")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", 0)) if os.getenv("ADMIN_TELEGRAM_ID") else None
DB_PATH = os.getenv("DB_PATH", "trades.db")

# Реквізити для оплати (можна змінити за потреби)
USDT_TRC20_WALLET = os.getenv("USDT_TRC20_WALLET", "ТВОЙ_USDT_TRC20_КОШЕЛЬОК")
USDT_BEP20_WALLET = os.getenv("USDT_BEP20_WALLET", "ТВОЙ_USDT_BEP20_КОШЕЛЬОК")

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

# --- БАЗА ДАНИХ (ІНІЦІАЛІЗАЦІЯ) ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                status TEXT DEFAULT 'free',
                trial_end TEXT,
                sub_end TEXT,
                bot_sub_end TEXT,
                signal_token TEXT,
                referrer_id INTEGER,
                lang TEXT DEFAULT 'ua',
                awaiting_support INTEGER DEFAULT 0
            )
        """)
        await db.commit()
    logger.info("Базу даних SQLite ініціалізовано.")

async def get_user_lang(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else "ua"

async def get_awaiting_support(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT awaiting_support FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def set_awaiting_support(user_id: int, val: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET awaiting_support = ? WHERE user_id = ?", (val, user_id))
        await db.commit()


# --- ФОНОВІ ЗАДАЧІ (CRON / TIMERS) ---
async def check_expired_trials():
    """Перевіряє закінчення терміну підписок щогодини."""
    while True:
        try:
            await asyncio.sleep(3600)
            now = datetime.now(timezone.utc)
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("""
                    SELECT user_id, lang FROM users 
                    WHERE status = 'active' AND sub_end IS NOT NULL AND sub_end < ?
                """, (now.isoformat(),)) as cursor:
                    expired_users = await cursor.fetchall()

                for user_id, lang in expired_users:
                    await db.execute("UPDATE users SET status = 'expired' WHERE user_id = ?", (user_id,))
                    await db.commit()
                    
                    user_lang = lang or "ua"
                    text = (
                        "⌛ <b>Термін вашої підписки на VIP-групу Kerdos закінчився.</b>\n\nДля продовження скористайтеся меню нижче."
                        if user_lang == "ua" else
                        "⌛ <b>Your Kerdos VIP group subscription has expired.</b>\n\nPlease use the menu below to renew your access."
                    )
                    try:
                        await bot.send_message(
                            chat_id=user_id,
                            text=text,
                            reply_markup=get_main_keyboard(user_id, user_lang),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logger.error(f"Failed to send expire notice to user {user_id}: {e}")
        except Exception as e:
            logger.error(f"Error in check_expired_trials loop: {e}")

async def monthly_inactive_users_reminder():
    """Перевіряє неактивних користувачів та надсилає їм мотиваційне повідомлення."""
    while True:
        try:
            await asyncio.sleep(86400)
            now = datetime.now(timezone.utc)
            
            if now.day == 1 and now.hour == 10:
                logger.info("⏳ Запуск щомісячної розсилки для неактивних користувачів...")
                
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("""
                        SELECT user_id, lang FROM users 
                        WHERE status IN ('expired', 'free') 
                           OR (sub_end IS NOT NULL AND sub_end < ?)
                    """, (now.isoformat(),)) as cursor:
                        inactive_users = await cursor.fetchall()

                count_sent = 0
                for u_id, lang in inactive_users:
                    u_lang = lang or "ua"
                    
                    if u_lang == "ua":
                        promo_text = (
                            "📈 <b>Час повертатися до торгівлі з Kerdos!</b>\n\n"
                            "Ринок дає чудові можливості! Не втрачайте шанс отримувати точні сигнали та заробляти разом з спільнотою.\n\n"
                            "💡 <b>Як ви можете заробляти зараз:</b>\n"
                            "1. 📊 <b>Оформити VIP-підписку ($20):</b> Отримуйте точні сигнали в реальному часі.\n"
                            "2. 🤖 <b>Автоматизувати торгівлю ($100):</b> Підключіть OKX Signal Bot для торгівлі 24/7.\n"
                            "3. 👥 <b>Безкоштовно через рефералку:</b> Запрошуйте друзів за вашим посиланням та отримуйте <b>+14 днів VIP за кожного</b>!\n\n"
                            "👇 Скористайтеся кнопками нижче, щоб повернутися в активну торгівлю:"
                        )
                    else:
                        promo_text = (
                            "📈 <b>Time to get back into trading with Kerdos!</b>\n\n"
                            "The market is moving! Don't miss out on high-accuracy signals and community profits.\n\n"
                            "💡 <b>How you can profit today:</b>\n"
                            "1. 📊 <b>VIP Access ($20):</b> Get real-time technical analysis & signals.\n"
                            "2. 🤖 <b>Automated Trading ($100):</b> Connect your OKX Signal Bot for 24/7 execution.\n"
                            "3. 👥 <b>Earn Free Days:</b> Invite friends and get <b>+14 FREE VIP days</b> for each referral!\n\n"
                            "👇 Use the menu below to extend your access:"
                        )

                    try:
                        await bot.send_message(
                            chat_id=u_id, 
                            text=promo_text, 
                            reply_markup=get_main_keyboard(u_id, u_lang), 
                            parse_mode="HTML"
                        )
                        count_sent += 1
                        await asyncio.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Не вдалося надіслати нагадування user {u_id}: {e}")

                if ADMIN_TELEGRAM_ID and bot:
                    await bot.send_message(
                        chat_id=ADMIN_TELEGRAM_ID,
                        text=f"📊 <b>Щомісячну розсилку завершено!</b>\nНадіслано повідомлень: {count_sent}",
                        parse_mode="HTML"
                    )

        except Exception as e:
            logger.error(f"Помилка в циклі monthly_inactive_users_reminder: {e}")


# --- LIFESPAN УПРАВЛІННЯ СТАРТОМ FASTAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(check_expired_trials())
    asyncio.create_task(monthly_inactive_users_reminder())
    yield

app = FastAPI(lifespan=lifespan)


# --- КНОПКИ ТА МЕНЮ ---
def get_main_keyboard(user_id: int, lang="ua"):
    if lang == "ua":
        keyboard = [
            [InlineKeyboardButton("⌛ Моя підписка", callback_data="btn_my_sub")],
            [InlineKeyboardButton("🎁 Отримати 14 днів FREE", callback_data="btn_free_trial")],
            [InlineKeyboardButton("👥 Реферальна програма", callback_data="btn_referral")],
            [InlineKeyboardButton("📊 Доступ до VIP-групи ($20 / 30 днів)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Автоматизація торгівлі ($100 / 30 днів)", callback_data="btn_buy_bot")],
            [InlineKeyboardButton("💬 Підтримка", callback_data="btn_support")],
            [InlineKeyboardButton("🌐 Мова / Language", callback_data="btn_change_lang")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("⌛ My Subscription", callback_data="btn_my_sub")],
            [InlineKeyboardButton("🎁 Get 14 Days FREE", callback_data="btn_free_trial")],
            [InlineKeyboardButton("👥 Referral Program", callback_data="btn_referral")],
            [InlineKeyboardButton("📊 VIP Group Access ($20 / 30 days)", callback_data="btn_buy_group")],
            [InlineKeyboardButton("🤖 Automated Trading ($100 / 30 days)", callback_data="btn_buy_bot")],
            [InlineKeyboardButton("💬 Support", callback_data="btn_support")],
            [InlineKeyboardButton("🌐 Language / Мова", callback_data="btn_change_lang")]
        ]
    
    # Додаємо кнопку адмін-панелі тільки для адміна
    if user_id == ADMIN_TELEGRAM_ID:
        keyboard.append([InlineKeyboardButton("👑 Адмін-панель", callback_data="btn_admin_panel")])

    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📊 Таблиця користувачів (/users)", callback_data="btn_admin_users")],
        [InlineKeyboardButton("➕ Видати VIP (/grant_vip)", callback_data="btn_admin_grant_vip")],
        [InlineKeyboardButton("🤖 Видати Bot (/grant_bot)", callback_data="btn_admin_grant_bot")],
        [InlineKeyboardButton("◀️ Назад у головне меню", callback_data="btn_admin_back")]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- WEBHOOK ДЛЯ TRADINGVIEW СИГНАЛІВ ---
@app.post("/webhook")
async def tradingview_webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"Отримано вебхук TradingView: {data}")

        raw_ticker = data.get("ticker", "UNKNOWN")
        clean_ticker = re.sub(r"\.P$", "", raw_ticker)
        action = str(data.get("action", "")).lower()
        price = data.get("price", "N/A")
        position_size = float(data.get("position_size", 0))
        market_position = str(data.get("market_position", "")).lower()

        if VIP_CHANNEL_ID and bot:
            action_emoji = "🟢 BUY" if "buy" in action else ("🔴 SELL" if "sell" in action else f"⚡ {action.upper()}")
            msg = (
                f"🚨 <b>KERDOS SIGNAL</b> 🚨\n\n"
                f"📊 <b>Монета:</b> #{clean_ticker}\n"
                f"🎯 <b>Дія:</b> {action_emoji}\n"
                f"💵 <b>Ціна:</b> {price}\n"
                f"⏰ <b>Час:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
            )
            try:
                await bot.send_message(chat_id=VIP_CHANNEL_ID, text=msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Помилка відправки сигналу в Telegram VIP-канал: {e}")

        now_str = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("""
                SELECT user_id, signal_token FROM users 
                WHERE signal_token IS NOT NULL 
                  AND signal_token != '' 
                  AND bot_sub_end IS NOT NULL 
                  AND bot_sub_end > ?
            """, (now_str,)) as cursor:
                bot_users = await cursor.fetchall()

        if not bot_users:
            return {"status": "ok", "message": "Немає активних бота-користувачів OKX"}

        okx_action = "enter_long"
        if position_size == 0:
            okx_action = "exit_long" if market_position == "flat" and "sell" in action else "exit_short"
        else:
            okx_action = "enter_long" if "buy" in action else "enter_short"

        instrument_id = f"{clean_ticker.replace('USDT', '')}-USDT-SWAP"
        
        results = []
        async with httpx.AsyncClient() as client:
            for u_id, token in bot_users:
                okx_payload = {
                    "signalToken": token,
                    "action": okx_action,
                    "instrument": instrument_id
                }
                try:
                    res = await client.post("https://www.okx.com/priapi/v5/rubik/stat/trading-bot/signal/generic", json=okx_payload, timeout=10.0)
                    results.append(f"User {u_id}: OKX status {res.status_code}")
                except Exception as ex:
                    results.append(f"User {u_id}: Error {ex}")

        if ADMIN_TELEGRAM_ID and bot:
            report = f"🤖 <b>OKX Signal Executed</b>\nTicker: {clean_ticker}\nAction: {okx_action}\nUsers notified: {len(results)}"
            try:
                await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=report, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Не вдалося надіслати звіт адміну: {e}")

        return {"status": "ok", "okx_results": results}

    except Exception as e:
        logger.error(f"Помилка в tradingview_webhook: {e}")
        return {"status": "error", "message": str(e)}


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

            # --- АДМІН-КОМАНДИ (Текстові) ---
            if update.message.text and user_id == ADMIN_TELEGRAM_ID:
                text = update.message.text.strip()

                if text.startswith("/"):
                    if text == "/users":
                        async with aiosqlite.connect(DB_PATH) as db:
                            async with db.execute("SELECT user_id, username, status, sub_end, bot_sub_end, signal_token FROM users") as cursor:
                                rows = await cursor.fetchall()

                        if not rows:
                            await bot.send_message(chat_id=chat_id, text="Таблиця користувачів порожня.")
                            return {"status": "ok"}

                        table_msg = "📊 <b>ТАБЛИЦЯ КОРИСТУВАЧІВ KERDOS:</b>\n\n"
                        for u_id, u_name, st, s_end, b_end, token in rows:
                            disp_name = f"@{u_name}" if u_name and u_name != "no_username" else "Без ніку"
                            has_token = "🔑 OKX: Так" if token else "❌ OKX: Ні"

                            table_msg += f"• <b>{disp_name}</b> (<code>{u_id}</code>)\n"
                            table_msg += f"  Статус: <code>{st}</code> | {has_token}\n"
                            if s_end: table_msg += f"  VIP до: {s_end[:10]}\n"
                            if b_end: table_msg += f"  Bot до: {b_end[:10]}\n"
                            table_msg += "-----------------------------\n"

                        await bot.send_message(chat_id=chat_id, text=table_msg[:4000], parse_mode="HTML")
                        return {"status": "ok"}

                    elif text.startswith("/grant_vip"):
                        parts = text.split()
                        if len(parts) == 3:
                            target_id = int(parts[1])
                            days = int(parts[2])
                            new_end = datetime.now(timezone.utc) + timedelta(days=days)

                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("UPDATE users SET status = 'active', sub_end = ? WHERE user_id = ?", (new_end.isoformat(), target_id))
                                await db.commit()

                            await bot.send_message(chat_id=chat_id, text=f"✅ Користувачу <code>{target_id}</code> видано VIP на {days} днів.", parse_mode="HTML")
                            return {"status": "ok"}

                    elif text.startswith("/grant_bot"):
                        parts = text.split()
                        if len(parts) == 3:
                            target_id = int(parts[1])
                            days = int(parts[2])
                            new_end = datetime.now(timezone.utc) + timedelta(days=days)

                            async with aiosqlite.connect(DB_PATH) as db:
                                await db.execute("UPDATE users SET bot_sub_end = ? WHERE user_id = ?", (new_end.isoformat(), target_id))
                                await db.commit()

                            await bot.send_message(chat_id=chat_id, text=f"✅ Користувачу <code>{target_id}</code> видано Signal Bot на {days} днів.", parse_mode="HTML")
                            return {"status": "ok"}

            # Обробка введення Signal Token (формат Token: xxx)
            if update.message.text and update.message.text.strip().lower().startswith("token:"):
                raw_token = update.message.text.strip().split(":", 1)[1].strip()
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET signal_token = ? WHERE user_id = ?", (raw_token, user_id))
                    await db.commit()

                success_text = (
                    "✅ <b>Signal Token успішно збережено!</b>\n\nВаш акаунт OKX прив'язано до системи сигналів <b>Kerdos</b>."
                    if user_lang == "ua" else
                    "✅ <b>Signal Token saved successfully!</b>\n\nYour OKX account is now linked to the <b>Kerdos</b> signal system."
                )
                await bot.send_message(chat_id=chat_id, text=success_text, parse_mode="HTML")
                return {"status": "ok"}

            # Обробка команди /start (Повний детальний текст)
            if update.message.text and update.message.text.startswith("/start"):
                ref_id = None
                parts = update.message.text.split()
                if len(parts) > 1 and parts[1].isdigit():
                    ref_id = int(parts[1])
                    if ref_id == user_id:
                        ref_id = None

                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()

                    if not row:
                        await db.execute(
                            "INSERT INTO users (user_id, username, referrer_id, lang) VALUES (?, ?, ?, 'ua')",
                            (user_id, username, ref_id)
                        )
                        await db.commit()

                welcome_text = (
                    f"Вітаємо, <b>{username}</b> у боті спільноти <b>Kerdos</b>! 🚀\n\n"
                    f"<b>Kerdos</b> — це закрита трейдинг-спільнота, де ви отримуєте точні сигнали для торгівлі криптою, "
                    f"автоматизовані стратегії для біржі OKX та підтримку 24/7.\n\n"
                    f"💡 <b>Що вам доступно в боті:</b>\n"
                    f"• <b>14 днів FREE:</b> Тестовий доступ до VIP-сигналів.\n"
                    f"• <b>VIP-група ($20/30 днів):</b> Точна аналітика та сигнали у реальному часі.\n"
                    f"• <b>OKX Signal Bot ($100/30 днів):</b> Повна автоматизація торгівлі на вашому акаунті.\n"
                    f"• <b>Реферальна система:</b> Запрошуйте друзів та отримуйте +14 днів за кожного!\n\n"
                    f"Оберіть потрібну дію у меню нижче:"
                    if user_lang == "ua" else
                    f"Welcome, <b>{username}</b> to the <b>Kerdos</b> community bot! 🚀\n\n"
                    f"<b>Kerdos</b> is an exclusive trading community offering highly accurate crypto signals, "
                    f"automated OKX trading strategies, and 24/7 support.\n\n"
                    f"💡 <b>Available options:</b>\n"
                    f"• <b>14 Days FREE:</b> Test access to VIP signals.\n"
                    f"• <b>VIP Group ($20/30 days):</b> Real-time market analysis and signals.\n"
                    f"• <b>OKX Signal Bot ($100/30 days):</b> Fully automated trading on your account.\n"
                    f"• <b>Referral Program:</b> Get +14 FREE days for every invited user!\n\n"
                    f"Choose an option from the menu below:"
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=welcome_text,
                    reply_markup=get_main_keyboard(user_id, user_lang),
                    parse_mode="HTML"
                )
                return {"status": "ok"}

            # Пересилання повідомлень підтримки
            if is_awaiting_support == 1 and update.message.text:
                await set_awaiting_support(user_id, 0)
                if ADMIN_TELEGRAM_ID:
                    admin_msg = f"📩 <b>ПОВІДОМЛЕННЯ В ПІДТРИМКУ</b>\nВід: @{username} (<code>{user_id}</code>)\n\nТекст:\n{update.message.text}"
                    await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=admin_msg, parse_mode="HTML")
                
                resp = "Дякуємо! Ваше повідомлення надіслано адміністратору." if user_lang == "ua" else "Thank you! Your message has been sent to support."
                await bot.send_message(chat_id=chat_id, text=resp, reply_markup=get_main_keyboard(user_id, user_lang))
                return {"status": "ok"}

        # 2. ОБРОБКА ІНЛАЙН-КНОПОК (CALLBACK QUERIES)
        elif update.callback_query:
            query = update.callback_query
            user_id = query.from_user.id
            chat_id = query.message.chat_id
            cb_data = query.data
            user_lang = await get_user_lang(user_id)

            await query.answer()

            # --- АДМІН-КНОПКИ ---
            if cb_data == "btn_admin_panel" and user_id == ADMIN_TELEGRAM_ID:
                await bot.send_message(
                    chat_id=chat_id,
                    text="👑 <b>АДМІНІСТРАТИВНА ПАНЕЛЬ KERDOS</b>\n\nОберіть потрібну дію з меню нижче:",
                    reply_markup=get_admin_keyboard(),
                    parse_mode="HTML"
                )

            elif cb_data == "btn_admin_users" and user_id == ADMIN_TELEGRAM_ID:
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT user_id, username, status, sub_end, bot_sub_end, signal_token FROM users") as cursor:
                        rows = await cursor.fetchall()

                if not rows:
                    await bot.send_message(chat_id=chat_id, text="Таблиця користувачів порожня.")
                else:
                    table_msg = "📊 <b>ТАБЛИЦЯ КОРИСТУВАЧІВ KERDOS:</b>\n\n"
                    for u_id, u_name, st, s_end, b_end, token in rows:
                        disp_name = f"@{u_name}" if u_name and u_name != "no_username" else "Без ніку"
                        has_token = "🔑 OKX: Так" if token else "❌ OKX: Ні"

                        table_msg += f"• <b>{disp_name}</b> (<code>{u_id}</code>)\n"
                        table_msg += f"  Статус: <code>{st}</code> | {has_token}\n"
                        if s_end: table_msg += f"  VIP до: {s_end[:10]}\n"
                        if b_end: table_msg += f"  Bot до: {b_end[:10]}\n"
                        table_msg += "-----------------------------\n"

                    await bot.send_message(chat_id=chat_id, text=table_msg[:4000], parse_mode="HTML")

            elif cb_data == "btn_admin_grant_vip" and user_id == ADMIN_TELEGRAM_ID:
                hint = "💡 <b>Для видачі VIP підписки надішліть команду:</b>\n\n<code>/grant_vip USER_ID DAYS</code>\n\nПриклад:\n<code>/grant_vip 123456789 30</code>"
                await bot.send_message(chat_id=chat_id, text=hint, parse_mode="HTML")

            elif cb_data == "btn_admin_grant_bot" and user_id == ADMIN_TELEGRAM_ID:
                hint = "💡 <b>Для видачі доступу до Signal Bot надішліть команду:</b>\n\n<code>/grant_bot USER_ID DAYS</code>\n\nПриклад:\n<code>/grant_bot 123456789 30</code>"
                await bot.send_message(chat_id=chat_id, text=hint, parse_mode="HTML")

            elif cb_data == "btn_admin_back":
                welcome_txt = "Головне меню спільноти <b>Kerdos</b>:"
                await bot.send_message(chat_id=chat_id, text=welcome_txt, reply_markup=get_main_keyboard(user_id, user_lang), parse_mode="HTML")

            # --- ОСНОВНІ КНОПКИ ДЛЯ КОРИСТУВАЧІВ ---
            elif cb_data == "btn_change_lang":
                new_lang = "en" if user_lang == "ua" else "ua"
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("UPDATE users SET lang = ? WHERE user_id = ?", (new_lang, user_id))
                    await db.commit()
                
                txt = "Мову змінено на українську 🇺🇦" if new_lang == "ua" else "Language changed to English 🇬🇧"
                await bot.send_message(chat_id=chat_id, text=txt, reply_markup=get_main_keyboard(user_id, new_lang))

            elif cb_data == "btn_my_sub":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT status, sub_end, bot_sub_end, signal_token FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()

                if row:
                    st, s_end, b_end, token = row
                    vip_info = s_end[:10] if s_end else ("Безліміт / Активно" if st == "active" else "Немає")
                    bot_info = b_end[:10] if b_end else "Немає"
                    token_info = "Прив'язано ✅" if token else "Не вказано ❌"

                    msg = (
                        f"📊 <b>ІНФОРМАЦІЯ ПРО ПІДПИСКУ:</b>\n\n"
                        f"• VIP-група: <b>{vip_info}</b>\n"
                        f"• OKX Signal Bot: <b>{bot_info}</b>\n"
                        f"• OKX Token: <b>{token_info}</b>"
                    ) if user_lang == "ua" else (
                        f"📊 <b>YOUR SUBSCRIPTION STATUS:</b>\n\n"
                        f"• VIP Group: <b>{vip_info}</b>\n"
                        f"• OKX Signal Bot: <b>{bot_info}</b>\n"
                        f"• OKX Token: <b>{token_info}</b>"
                    )
                else:
                    msg = "Користувача не знайдено." if user_lang == "ua" else "User not found."

                await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", reply_markup=get_main_keyboard(user_id, user_lang))

            elif cb_data == "btn_free_trial":
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT status FROM users WHERE user_id = ?", (user_id,)) as cursor:
                        row = await cursor.fetchone()
                    
                    if row and row[0] == "free":
                        trial_end = datetime.now(timezone.utc) + timedelta(days=14)
                        await db.execute("UPDATE users SET status = 'active', sub_end = ? WHERE user_id = ?", (trial_end.isoformat(), user_id))
                        await db.commit()
                        txt = "🎉 Вітаємо! Вам активовано <b>14 днів безкоштовного доступу</b>!" if user_lang == "ua" else "🎉 Congratulations! You activated <b>14 FREE trial days</b>!"
                    else:
                        txt = "❌ Ви вже використовували пробний період або маєте активну підписку." if user_lang == "ua" else "❌ You have already used your trial or have an active access."

                await bot.send_message(chat_id=chat_id, text=txt, parse_mode="HTML", reply_markup=get_main_keyboard(user_id, user_lang))

            elif cb_data == "btn_referral":
                ref_link = f"https://t.me/{(await bot.get_me()).username}?start={user_id}"
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,)) as cursor:
                        count = (await cursor.fetchone())[0]

                txt = (
                    f"👥 <b>РЕФЕРАЛЬНА ПРОГРАМА KERDOS</b>\n\nЗапрошуйте друзів та отримуйте <b>+14 днів VIP</b> за кожного залученого реферала!\n\n"
                    f"🔗 Ваше посилання:\n<code>{ref_link}</code>\n\n"
                    f"📊 Запрошено користувачів: <b>{count}</b>"
                ) if user_lang == "ua" else (
                    f"👥 <b>KERDOS REFERRAL PROGRAM</b>\n\nInvite friends and get <b>+14 FREE VIP Days</b> for each referral!\n\n"
                    f"🔗 Your link:\n<code>{ref_link}</code>\n\n"
                    f"📊 Invited users: <b>{count}</b>"
                )
                await bot.send_message(chat_id=chat_id, text=txt, parse_mode="HTML", reply_markup=get_main_keyboard(user_id, user_lang))

            # --- ОПЛАТА VIP-ГРУПИ ($20) ---
            elif cb_data == "btn_buy_group":
                pay_txt = (
                    f"📊 <b>ОФОРМЛЕННЯ ПІДПИСКИ НА VIP-ГРУПУ ($20 / 30 ДНІВ)</b>\n\n"
                    f"Для оплати перекажіть <b>20 USDT</b> на один із гаманців нижче:\n\n"
                    f"🌐 <b>USDT (TRC20):</b>\n<code>{USDT_TRC20_WALLET}</code>\n\n"
                    f"🌐 <b>USDT (BEP20):</b>\n<code>{USDT_BEP20_WALLET}</code>\n\n"
                    f"⚠️ <b>Після здійснення переказу:</b>\n"
                    f"Натисніть кнопку <b>💬 Підтримка</b> у головному меню та надішліть скріншот чека або квитанцію про оплату. "
                    f"Адміністратор перевірить транзакцію та надасть вам доступ!"
                ) if user_lang == "ua" else (
                    f"📊 <b>VIP GROUP SUBSCRIPTION ($20 / 30 DAYS)</b>\n\n"
                    f"To pay, please send <b>20 USDT</b> to one of the following wallets:\n\n"
                    f"🌐 <b>USDT (TRC20):</b>\n<code>{USDT_TRC20_WALLET}</code>\n\n"
                    f"🌐 <b>USDT (BEP20):</b>\n<code>{USDT_BEP20_WALLET}</code>\n\n"
                    f"⚠️ <b>After payment:</b>\n"
                    f"Click <b>💬 Support</b> in the main menu and send your payment screenshot or TxID. "
                    f"An admin will review it and activate your access!"
                )
                await bot.send_message(chat_id=chat_id, text=pay_txt, parse_mode="HTML", reply_markup=get_main_keyboard(user_id, user_lang))

            # --- ОПЛАТА АВТОМАТИЗАЦІЇ ($100) ТА ВВЕДЕННЯ TOKEN ---
            elif cb_data == "btn_buy_bot":
                pay_txt = (
                    f"🤖 <b>АВТОМАТИЗАЦІЯ ТОРГІВЛІ OKX ($100 / 30 ДНІВ)</b>\n\n"
                    f"Для підключення автоторгівлі перекажіть <b>100 USDT</b> на один із гаманців:\n\n"
                    f"🌐 <b>USDT (TRC20):</b>\n<code>{USDT_TRC20_WALLET}</code>\n\n"
                    f"🌐 <b>USDT (BEP20):</b>\n<code>{USDT_BEP20_WALLET}</code>\n\n"
                    f"🔑 <b>ПРИВ'ЯЗКА OKX SIGNAL BOT:</b>\n"
                    f"Після оплати створіть сигнал-бота на біржі OKX та надішліть свій Token сюди у чат у форматі:\n"
                    f"<code>Token: ваш_токен_тут</code>\n\n"
                    f"📩 Після переказу коштів також надішліть чек через кнопку <b>💬 Підтримка</b>."
                ) if user_lang == "ua" else (
                    f"🤖 <b>OKX AUTOMATED TRADING ($100 / 30 DAYS)</b>\n\n"
                    f"To connect automated trading, send <b>100 USDT</b> to one of these wallets:\n\n"
                    f"🌐 <b>USDT (TRC20):</b>\n<code>{USDT_TRC20_WALLET}</code>\n\n"
                    f"🌐 <b>USDT (BEP20):</b>\n<code>{USDT_BEP20_WALLET}</code>\n\n"
                    f"🔑 <b>LINKING YOUR OKX SIGNAL BOT:</b>\n"
                    f"After payment, create a Signal Bot on OKX and send your Token to this chat in the format:\n"
                    f"<code>Token: your_token_here</code>\n\n"
                    f"📩 Also send your payment proof using <b>💬 Support</b> button."
                )
                await bot.send_message(chat_id=chat_id, text=pay_txt, parse_mode="HTML", reply_markup=get_main_keyboard(user_id, user_lang))

            elif cb_data == "btn_support":
                await set_awaiting_support(user_id, 1)
                txt = "✍️ Напишіть ваше запитання або надішліть чек про оплату наступним повідомленням:" if user_lang == "ua" else "✍️ Please send your question or payment receipt in the next message:"
                await bot.send_message(chat_id=chat_id, text=txt)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Помилка в telegram_webhook: {e}")
        return {"status": "error"}
