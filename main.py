import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import ADMIN_ID, TELEGRAM_BOT_TOKEN


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение в ответ на команду /start."""
    await update.message.reply_text("Привет! Я бот для продажи обуви.")


async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает процесс добавления товара, доступно только администратору."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return
    await update.message.reply_text("Начинаем добавление нового товара...")


def main() -> None:
    """Основная функция для запуска бота."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addproduct", add_product_start))

    application.run_polling()


if __name__ == '__main__':
    main()