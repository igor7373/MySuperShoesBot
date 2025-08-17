import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, JobQueue, MessageHandler,
                          filters, CallbackQueryHandler)

from config import ADMIN_ID, CHANNEL_ID, TELEGRAM_BOT_TOKEN, BOT_USERNAME
from database import (add_product, get_all_products, get_product_by_id, init_db,
                      set_product_sold, update_message_id, update_product_sizes,
                      delete_product_by_id)

# Определяем состояния для диалога
PHOTO, SELECTING_SIZES, ENTERING_PRICE, AWAITING_PROOF, AWAITING_NAME, AWAITING_PHONE, AWAITING_CITY, AWAITING_POST_OFFICE = range(8)


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


async def payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Начинает гибридный процесс бронирования: визуально убирает размер из канала,
    запускает таймер, но НЕ изменяет базу данных.
    """
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    # Извлекаем данные (формат: payment_{type}_{product_id}_{size})
    _, payment_type, product_id_str, selected_size = query.data.split('_')
    product_id = int(product_id_str)

    # Шаг 1: Получаем товар и визуально убираем размер из поста в канале
    product = get_product_by_id(product_id)
    print(f"DEBUG INFO: Пытаюсь обработать заказ для продукта: {product}")
    if not product or not product['message_id']:
        await query.message.reply_text("Вибачте, сталася помилка з товаром. Спробуйте пізніше.")
        return ConversationHandler.END

    # Формируем новый список размеров без забронированного
    current_sizes = product['sizes'].split(',')
    if selected_size in current_sizes:
        current_sizes.remove(selected_size)

    # Редактируем сообщение в канале
    try:
        if current_sizes:
            new_sizes_str = ", ".join(sorted(current_sizes, key=int))
            new_caption = (f"Натуральна шкіра\n"
                           f"{new_sizes_str} розмір\n"
                           f"{product['price']} грн наявність")
            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]])
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=product['message_id'],
                caption=new_caption,
                reply_markup=keyboard
            )
        else:  # Если это был последний размер
            new_caption = (f"Натуральна шкіра\n"
                           f"ПРОДАНО\n"
                           f"{product['price']} грн наявність")
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID,
                message_id=product['message_id'],
                caption=new_caption,
                reply_markup=None
            )
    except Exception as e:
        print(f"Не удалось отредактировать сообщение в канале при бронировании: {e}")
        # Если не удалось отредактировать пост, не стоит продолжать бронь
        await query.message.reply_text("Вибачте, сталася помилка. Не вдалося забронювати товар. Спробуйте пізніше.")
        return ConversationHandler.END

    # Шаг 2: Запускаем таймер на 30 минут для отмены брони
    job = context.job_queue.run_once(
        cancel_reservation,
        1800,  # 30 минут
        data={'user_id': user_id, 'product_id': product_id, 'selected_size': selected_size},
        name=f"reservation_{user_id}_{product_id}"
    )

    # Шаг 3: Сохраняем данные для следующего шага
    context.user_data['reservation_job'] = job
    context.user_data['product_id'] = product_id
    context.user_data['selected_size'] = selected_size

    # Шаг 4: Информируем пользователя
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "Реквізити для оплати: [Тут будуть ваші реквізити].\n"
        "Товар тимчасово заброньовано. У вас є 30 хвилин, щоб надіслати скріншот або файл, що підтверджує оплату. "
        "В іншому випадку бронь буде скасована, і товар знову стане доступним."
    )

    return AWAITING_PROOF


async def cancel_reservation(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отменяет визуальную бронь, возвращая посту в канале исходное состояние.
    НЕ изменяет базу данных.
    """
    job_data = context.job.data
    product_id = job_data['product_id']
    user_id = job_data['user_id']
    selected_size = job_data['selected_size']

    # Получаем актуальное состояние товара из БД (там размер не удалялся)
    product = get_product_by_id(product_id)
    if not product or not product['message_id']:
        print(f"Ошибка отмены брони: товар {product_id} или message_id не найден.")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"На жаль, час на оплату товару (ID: {product_id}, розмір: {selected_size}) вичерпано. Ваша бронь скасовано."
        )
        return

    # Восстанавливаем подпись в посте канала, используя данные из БД как источник правды
    original_sizes_str = ", ".join(sorted(product['sizes'].split(','), key=int))

    new_caption = (f"Натуральна шкіра\n"
                   f"{original_sizes_str} розмір\n"
                   f"{product['price']} грн наявність")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]])

    try:
        await context.bot.edit_message_caption(
            chat_id=CHANNEL_ID,
            message_id=product['message_id'],
            caption=new_caption,
            reply_markup=keyboard
        )
    except Exception as e:
        print(f"Не удалось обновить сообщение в канале при отмене брони: {e}")

    # Уведомляем пользователя
    await context.bot.send_message(
        chat_id=user_id,
        text=f"На жаль, час на оплату товару (ID: {product_id}, розмір: {selected_size}) вичерпано. Ваша бронь скасовано. Товар знову доступний для покупки."
    )


async def proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Принимает подтверждение оплаты, отменяет таймер и запрашивает ФИО."""
    # Отменяем таймер отмены брони
    job = context.user_data.get('reservation_job')
    if job:
        job.schedule_removal()
        print(f"Таймер брони {job.name} отменен.")

    file_id = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id

    if file_id:
        context.user_data['proof_file_id'] = file_id

    await update.message.reply_text(
        "Дякуємо! Ваше підтвердження отримано. "
        "Будь ласка, введіть Ваше ПІБ (прізвище, ім'я, по батькові)."
    )
    return AWAITING_NAME


async def name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет ФИО и запрашивает номер телефона."""
    context.user_data['full_name'] = update.message.text
    await update.message.reply_text("Введіть Ваш номер телефону.")
    return AWAITING_PHONE


async def phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет телефон и запрашивает город."""
    context.user_data['phone_number'] = update.message.text
    await update.message.reply_text("Введіть Ваше місто.")
    return AWAITING_CITY


async def city_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет город и запрашивает отделение Новой Почты."""
    context.user_data['city'] = update.message.text
    await update.message.reply_text("Введіть номер відділення Нової Пошти.")
    return AWAITING_POST_OFFICE


async def post_office_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет отделение, завершает сбор данных и диалог."""
    context.user_data['post_office'] = update.message.text

    # На этом этапе все данные собраны.
    # Логика отправки менеджеру будет на следующем шаге.
    await update.message.reply_text(
        "Дякуємо! Всі дані отримано. Ваше замовлення передається менеджеру на перевірку."
    )

    return ConversationHandler.END


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

    payment_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(payment_callback, pattern='^payment_')],
        states={
            AWAITING_PROOF: [MessageHandler(filters.PHOTO | filters.Document.ALL, proof_received)],
            AWAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_received)],
            AWAITING_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_received)],
            AWAITING_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_received)],
            AWAITING_POST_OFFICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_office_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(payment_conv_handler)
    application.add_handler(CommandHandler("catalog", show_catalog))
    application.add_handler(CommandHandler("delete", show_delete_list))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern='^del_'))
    application.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern='^confirm_del_'))
    application.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern='^cancel_del$'))
    application.add_handler(CallbackQueryHandler(republish_callback, pattern='^repub_'))
    application.add_handler(CallbackQueryHandler(size_callback, pattern='^ps_'))

    application.run_polling()


if __name__ == '__main__':
    main()