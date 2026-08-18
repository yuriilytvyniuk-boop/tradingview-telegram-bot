import os
import logging
from fastapi import FastAPI, Request, HTTPException
from telegram import Bot

# Налаштування логування для відображення в консолі Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Зчитування змінних оточення
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    logger.error("Критична помилка: TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID не вказані в Environment Variables!")

bot = Bot(token=TELEGRAM_BOT_TOKEN) if TELEGRAM_BOT_TOKEN else None

@app.get("/")
async def root():
    return {"status": "ok", "message": "Kerdos Bot Webhook Service is running"}

@app.post("/webhook")
async def webhook(request: Request):
    if not bot:
        logger.error("Спроба викликати /webhook, але бот не ініціалізований (відсутній токен)")
        raise HTTPException(status_code=500, detail="Bot token missing")

    try:
        # Зчитуємо та виводимо сирий JSON від TradingView
        data = await request.json()
        logger.info(f"Отримано сигнал від TradingView: {data}")

        ticker = data.get("ticker", "N/A")
        price = data.get("price", "N/A")
        action = data.get("action", "N/A").upper()

        # Формуємо текст повідомлення
        message_text = (
            f"📊 **Новий сигнал TradingView**\n\n"
            f"• **Тикер:** {ticker}\n"
            f"• **Дія:** {action}\n"
            f"• **Ціна:** {price}"
        )

        # Відправляємо повідомлення в Telegram
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message_text,
            parse_mode="Markdown"
        )
        logger.info("Повідомлення успішно відправлено в Telegram!")
        
        return {"status": "success"}

    except Exception as e:
        logger.error(f"Помилка при обробці вебхука: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
