import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Предполагается, что файл config.py находится в той же директории
from config import TELEGRAM_BOT_TOKEN, DISPATCH_CHANNEL_ID

# Включаем логирование для отладки
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет сообщение с инлайн-кнопкой в канал по команде /start."""
    keyboard = [
        [
            InlineKeyboardButton(
                "Тестовая кнопка", callback_data="test_button_pressed"
            )
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(
            chat_id=DISPATCH_CHANNEL_ID,
            text="Это тестовый пост.",
            reply_markup=reply_markup,
        )
        await update.message.reply_text("Тестовое сообщение отправлено в канал отправок.")
    except Exception as e:
        error_text = f"Не удалось отправить сообщение в канал. Ошибка: {e}"
        print(error_text)
        await update.message.reply_text(error_text)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие инлайн-кнопки."""
    query = update.callback_query

    # Выводим в консоль для подтверждения срабатывания
    print("--- КНОПКА НАЖАТА! ---")

    # Отвечаем на колбэк, чтобы убрать "часики" на кнопке у пользователя
    await query.answer(text="Нажатие зарегистрировано!", show_alert=True)

    # Редактируем исходное сообщение
    try:
        await query.edit_message_text(text="Тест пройден успешно!")
        print("--- Сообщение в канале успешно изменено. ---")
    except Exception as e:
        print(f"--- Ошибка при редактировании сообщения: {e} ---")

def main() -> None:
    """Основная функция для запуска бота."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))

    print("--- Тестовый бот запущен. Отправьте ему /start в личном чате. ---")
    application.run_polling()

if __name__ == "__main__":
    main()