import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters,
                          CallbackQueryHandler)

from config import ADMIN_ID, CHANNEL_ID, TELEGRAM_BOT_TOKEN, BOT_USERNAME
from database import (add_product, get_all_products, get_product_by_id, init_db,
                      set_product_sold, update_message_id, update_product_sizes,
                      delete_product_by_id)

# Определяем состояния для диалога
PHOTO, SELECTING_SIZES, ENTERING_PRICE = range(3)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает команду /start.
    Если команда вызвана с параметром (deep link), запускает процесс покупки.
    Иначе, отправляет приветственное сообщение.
    """
    args = context.args
    if args and args[0].startswith('buy_'):
        try:
            product_id = int(args[0].split('_')[1])
        except (IndexError, ValueError):
            await update.message.reply_text("Некоректне посилання для покупки.")
            return

        product = get_product_by_id(product_id)
        user_id = update.effective_user.id

        if not product or not product['sizes']:
            await context.bot.send_message(
                chat_id=user_id,
                text="Вибачте, цей товар більше не доступний."
            )
            return

        # Отправляем фото/видео товара в личный чат
        file_id = product['file_id']
        if file_id.startswith("BAAC"):
            await context.bot.send_video(chat_id=user_id, video=file_id)
        else:
            await context.bot.send_photo(chat_id=user_id, photo=file_id)

        # Создаем клавиатуру с доступными размерами
        available_sizes = product['sizes'].split(',')
        keyboard_buttons = [
            InlineKeyboardButton(size, callback_data=f"ps_{product['id']}_{size}")
            for size in available_sizes
        ]
        keyboard = [keyboard_buttons[i:i + 5] for i in range(0, len(keyboard_buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(
            chat_id=user_id,
            text="Оберіть ваш розмір:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text("Привіт! Я бот для продажу взуття.")


async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления товара и запрашивает фото."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Ця команда доступна лише адміністратору.")
        return ConversationHandler.END
    await update.message.reply_text("Завантажте фотографію товару.")
    return PHOTO


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает полученное фото/видео/документ и предлагает выбрать размеры."""
    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        file_id = update.message.video.file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    context.user_data['photo_id'] = file_id
    context.user_data['selected_sizes'] = []

    keyboard = create_sizes_keyboard([])
    await update.message.reply_text("Медіафайл отримано. Оберіть потрібні розміри:",
                                    reply_markup=keyboard)
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
        InlineKeyboardButton("⬅️ Скасувати останнє", callback_data='undo'),
        InlineKeyboardButton("✅ Зберегти", callback_data='save')
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
                await query.answer(text="Будь ласка, оберіть хоча б один розмір.", show_alert=True)
                return SELECTING_SIZES
            await query.edit_message_text("Розміри збережено. Введіть ціну товару у гривнях.")
            return ENTERING_PRICE
        elif data == 'undo':
            if selected_sizes:
                selected_sizes.pop()
        else:
            selected_sizes.append(int(data))

        print(f"Sizes after operation: {selected_sizes}")

        keyboard = create_sizes_keyboard(selected_sizes)
        text = "Выбрано: " + ", ".join(map(str, sorted(selected_sizes))) if selected_sizes else "Оберіть потрібні розміри:"
        
        print("--- Preparing to edit message ---")
        await query.edit_message_text(text=text, reply_markup=keyboard)
        print("--- Message edited successfully, handler finished ---")

        return SELECTING_SIZES
    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в select_size_callback: {e}")
        return SELECTING_SIZES


async def price_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает цену, публикует товар в канал и завершает диалог."""
    price_text = update.message.text
    if not price_text.isdigit():
        await update.message.reply_text("Будь ласка, введіть коректну ціну у вигляді числа.")
        return ENTERING_PRICE

    context.user_data['price'] = int(price_text)

    # Собираем данные
    file_id = context.user_data['photo_id']
    selected_sizes = context.user_data['selected_sizes']
    price = context.user_data['price']

    # Добавляем товар в базу и получаем его ID
    product_id = add_product(file_id=file_id, price=price, sizes=selected_sizes)

    # Готовим пост для канала
    sizes_str = ", ".join(map(str, sorted(selected_sizes)))
    caption = (f"Натуральна шкіра\n"
               f"{sizes_str} розмір\n"
               f"{price} грн наявність")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product_id}")]]
    )

    # Отправляем пост в канал, определяя тип медиа
    if file_id.startswith("BAAC"):  # Примерный префикс для видео
        sent_message = await context.bot.send_video(
            chat_id=CHANNEL_ID, video=file_id, caption=caption, reply_markup=keyboard
        )
    else:
        sent_message = await context.bot.send_photo(
            chat_id=CHANNEL_ID, photo=file_id, caption=caption, reply_markup=keyboard
        )

    # Сохраняем message_id в базу
    update_message_id(product_id, sent_message.message_id)

    await update.message.reply_text("Товар успішно додано та опубліковано в каналі.")
    return ConversationHandler.END


async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выводит каталог товаров, доступных для покупки."""
    products = get_all_products()

    if not products:
        await update.message.reply_text("Каталог поки що порожній.")
        return

    for product in products:
        caption = f"Ціна: {product['price']} грн.\nРозміри в наявності: {product['sizes']}"

        is_admin = update.effective_user.id == ADMIN_ID
        if is_admin:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔁 Опублікувати знову", callback_data=f"repub_{product['id']}")]]
            )
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]]
            )

        # Отправляем медиа в зависимости от его типа
        if product['file_id'].startswith("BAAC"):
            await update.message.reply_video(
                video=product['file_id'], caption=caption, reply_markup=keyboard
            )
        else:
            await update.message.reply_photo(
                photo=product['file_id'], caption=caption, reply_markup=keyboard
            )


async def size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор размера и предлагает варианты оплаты."""
    query = update.callback_query
    await query.answer()

    # Извлекаем данные из callback_data (формат: ps_{product_id}_{size})
    _, product_id, selected_size = query.data.split('_')

    text = (f"Ви обрали розмір {selected_size}. Товар буде заброньовано для вас на 30 хвилин "
            f"після отримання реквізитів.\n\nОберіть тип оплати:")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Передплата", callback_data=f"payment_prepay_{product_id}_{selected_size}")],
        [InlineKeyboardButton("Повна оплата", callback_data=f"payment_full_{product_id}_{selected_size}")]
    ])

    await query.message.reply_text(text, reply_markup=keyboard)


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор типа оплаты, бронирует товар и обновляет каталог."""
    query = update.callback_query
    await query.answer()

    # Извлекаем данные (формат: payment_{type}_{product_id}_{size})
    _, payment_type, product_id, selected_size = query.data.split('_')
    product_id = int(product_id)

    # Шаг А: Информирование
    await query.message.reply_text(
        "Реквізити для оплати: [Тут будуть ваші реквізити].\n"
        "Після оплати надішліть скріншот адміністратору."
    )
    # Убираем кнопки после выбора
    await query.edit_message_reply_markup(reply_markup=None)

    # Шаг Б: Получение данных о товаре
    product = get_product_by_id(product_id)
    if not product or not product['message_id']:
        print(f"Ошибка: не найден товар {product_id} или message_id для обновления каталога.")
        return

    # Шаг В: Обновление базы данных
    current_sizes = product['sizes'].split(',')
    if selected_size in current_sizes:
        current_sizes.remove(selected_size)

    new_sizes_str = ",".join(sorted(current_sizes, key=int))
    update_product_sizes(product_id, new_sizes_str)

    # Шаг Г: Обновление каталога
    if new_sizes_str:
        new_caption = (f"Натуральна шкіра\n"
                       f"{new_sizes_str} розмір\n"
                       f"{product['price']} грн наявність")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]])
        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=product['message_id'],
            caption=new_caption, reply_markup=keyboard
        )
    else:  # Все размеры проданы
        new_caption = (f"Натуральна шкіра\n"
                       f"ПРОДАНО\n"
                       f"{product['price']} грн наявність")
        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=product['message_id'],
            caption=new_caption, reply_markup=None
        )


async def republish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие на кнопку 'Опубликовать заново'."""
    try:
        print("\n--- republish_callback started ---")
        query = update.callback_query
        await query.answer()

        product_id = int(query.data.split('_')[1])
        print(f"Received product_id: {product_id}")
        product = get_product_by_id(product_id)

        if not product:
            await query.edit_message_text("Помилка: товар не знайдено.")
            return

        # Формируем подпись и клавиатуру для поста в канале
        sizes_str = ", ".join(sorted(product['sizes'].split(',')))
        caption = (f"Натуральна шкіра\n"
                   f"{sizes_str} розмір\n"
                   f"{product['price']} грн наявність")
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product_id}")]]
        )

        # Отправляем пост в канал, определяя тип медиа
        file_id = product['file_id']
        print(f"Attempting to send post for product {product_id} to channel...")
        if file_id.startswith("BAAC"):
            sent_message = await context.bot.send_video(
                chat_id=CHANNEL_ID, video=file_id, caption=caption, reply_markup=keyboard)
        else:
            sent_message = await context.bot.send_photo(
                chat_id=CHANNEL_ID, photo=file_id, caption=caption, reply_markup=keyboard)
        print(f"Post sent successfully. New message_id: {sent_message.message_id}")

        # Обновляем message_id в базе и уведомляем администратора
        print(f"Attempting to update message_id for product {product_id} in DB...")
        update_message_id(product_id, sent_message.message_id)

        print("Attempting to send confirmation to admin...")
        await query.message.reply_text(f"Товар ID: {product_id} успішно опубліковано повторно.")
        # Убираем кнопку "Опубликовать заново" из сообщения в каталоге
        await query.edit_message_reply_markup(reply_markup=None)
        print("--- republish_callback finished successfully ---")
    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА в republish_callback: {e}")


async def show_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выводит список товаров для удаления."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Ця команда доступна лише адміністратору.")
        return

    products = get_all_products()

    if not products:
        await update.message.reply_text("У каталозі немає товарів для видалення.")
        return

    await update.message.reply_text("Оберіть товар, який хочете видалити:")
    for product in products:
        caption = f"ID: {product['id']}\nЦіна: {product['price']} грн.\nРозміри: {product['sizes']}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Видалити цей товар", callback_data=f"del_{product['id']}")]]
        )

        if product['file_id'].startswith("BAAC"):
            await update.message.reply_video(
                video=product['file_id'], caption=caption, reply_markup=keyboard
            )
        else:
            await update.message.reply_photo(
                photo=product['file_id'], caption=caption, reply_markup=keyboard
            )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запрашивает подтверждение на удаление товара."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[1])

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Так, видалити", callback_data=f"confirm_del_{product_id}"),
            InlineKeyboardButton("❌ Ні, скасувати", callback_data="cancel_del")
        ]
    ])
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"Ви впевнені, що хочете видалити товар ID: {product_id}?",
        reply_markup=keyboard
    )


async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Окончательно удаляет товар из БД и канала."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[2])
    product = get_product_by_id(product_id)

    if product and product['message_id']:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=product['message_id'])
        except Exception as e:
            print(f"Не удалось удалить сообщение {product['message_id']} из канала: {e}")

    delete_product_by_id(product_id)
    await query.edit_message_text("Товар успішно видалено.")


async def cancel_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отменяет процесс удаления товара, редактируя сообщение."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Видалення скасовано.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущий диалог."""
    await update.message.reply_text("Дію скасовано.")
    return ConversationHandler.END


def main() -> None:
    """Основная функция для запуска бота."""
    init_db()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('addproduct', add_product_start)],
        states={
            PHOTO: [MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.IMAGE, photo_received)],
            SELECTING_SIZES: [CallbackQueryHandler(select_size_callback)],
            ENTERING_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("catalog", show_catalog))
    application.add_handler(CommandHandler("delete", show_delete_list))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern='^del_'))
    application.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern='^confirm_del_'))
    application.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern='^cancel_del$'))
    application.add_handler(CallbackQueryHandler(republish_callback, pattern='^repub_'))
    application.add_handler(CallbackQueryHandler(size_callback, pattern='^ps_'))
    application.add_handler(CallbackQueryHandler(payment_callback, pattern='^payment_'))

    application.run_polling()


if __name__ == '__main__':
    main()