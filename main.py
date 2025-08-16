import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters,
                          CallbackQueryHandler)

from config import ADMIN_ID, TELEGRAM_BOT_TOKEN

# Определяем состояния для диалога
PHOTO, SELECTING_SIZES, ENTERING_PRICE = range(3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение в ответ на команду /start."""
    await update.message.reply_text("Привет! Я бот для продажи обуви.")


async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления товара и запрашивает фото."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Эта команда доступна только администратору.")
        return ConversationHandler.END
    await update.message.reply_text("Загрузите фотографию товара.")
    return PHOTO


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает полученное фото и предлагает выбрать размеры."""
    photo_file = update.message.photo[-1]
    context.user_data['photo_id'] = photo_file.file_id
    context.user_data['selected_sizes'] = []

    keyboard = create_sizes_keyboard([])
    await update.message.reply_text(
        "Фото получено. Выберите нужные размеры:", reply_markup=keyboard
    )
    return SELECTING_SIZES


def create_sizes_keyboard(selected_sizes: list[int]) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора размеров."""
    keyboard = []
    all_sizes = list(range(28, 49))
    row = []
    for size in all_sizes:
        text = f"[ {size} ]" if size in selected_sizes else f"  {size}  "
        row.append(InlineKeyboardButton(text, callback_data=str(size)))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Отменить последнее", callback_data='undo'),
        InlineKeyboardButton("✅ Сохранить", callback_data='save')
    ])
    return InlineKeyboardMarkup(keyboard)


async def select_size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатия на кнопки выбора размеров."""
    try:
        query = update.callback_query
        await query.answer()
        data = query.data

        print("\n--- Callback handler started ---")
        print(f"Data received: {data}")
        selected_sizes = context.user_data.get('selected_sizes', [])
        print(f"Sizes before operation: {selected_sizes}")

        if data == 'save':
            if not selected_sizes:
                await query.answer(text="Пожалуйста, выберите хотя бы один размер.", show_alert=True)
                return SELECTING_SIZES
            await query.edit_message_text("Размеры сохранены. Введите цену товара в гривнах.")
            return ENTERING_PRICE
        elif data == 'undo':
            if selected_sizes:
                selected_sizes.pop()
        else:
            selected_sizes.append(int(data))

        print(f"Sizes after operation: {selected_sizes}")

        keyboard = create_sizes_keyboard(selected_sizes)
        text = "Выбрано: " + ", ".join(map(str, sorted(selected_sizes))) if selected_sizes else "Выберите нужные размеры:"
        
        print("--- Preparing to edit message ---")
        await query.edit_message_text(text=text, reply_markup=keyboard)
        print("--- Message edited successfully, handler finished ---")

        return SELECTING_SIZES
    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в select_size_callback: {e}")
        return SELECTING_SIZES


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог."""
    await update.message.reply_text("Действие отменено.")
    return ConversationHandler.END


def main() -> None:
    """Основная функция для запуска бота."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('addproduct', add_product_start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO, photo_received)],
            SELECTING_SIZES: [CallbackQueryHandler(select_size_callback)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)

    application.run_polling()


if __name__ == '__main__':
    main()