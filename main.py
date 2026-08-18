import os
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Bot

# Налаштування логування для консолі Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Токен бота зі змінних оточення
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Гарантоване отримання CHAT_ID у вигляді int
RAW_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003940810691")
try:
    TELEGRAM_CHAT_ID = int(RAW_CHAT_ID)
except ValueError:
    logger.warning(f"Не вдалося розпарсити TELEGRAM_CHAT_ID '{RAW_CHAT_ID}', використовуємо дефолтний ID.")
    TELEGRAM_CHAT_ID = -1003940810691

if not TELEGRAM_BOT_TOKEN:
    logger.error("Критична помилка: TELEGRAM_BOT_TOKEN не вказано в Environment Variables!")

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None


@app.get("/")
async def root():
    return {"status": "ok", "message": "Kerdos Bot Webhook Service is running"}


@app.post("/webhook")
async def webhook(request: Request):
    if not bot:
        logger.error("Спроба викликати /webhook, але бот не ініціалізований.")
        raise HTTPException(status_code=500, detail="Bot token missing")

    try:
        data = await request.json()
        logger.info(f"Отримано сигнал від TradingView: {data}")

        ticker = data.get("ticker", "N/A")
        price = data.get("price", "N/A")
        action = str(data.get("action", "N/A")).upper()

        message_text = (
            f"📊 **Новий сигнал TradingView**\n\n"
            f"• **Тикер:** {ticker}\n"
            f"• **Дія:** {action}\n"
            f"• **Ціна:** {price}"
        )

        # Відправка повідомлення з явно переданим цілочисельним ID
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message_text,
            parse_mode="Markdown"
        )
        logger.info(f"Повідомлення успішно відправлено в чат {TELEGRAM_CHAT_ID}!")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Помилка при обробці вебхука: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
