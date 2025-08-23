import asyncio
import json

from apscheduler.jobstores.base import JobLookupError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, JobQueue, MessageHandler,
                          filters, CallbackQueryHandler)

from config import (ADMIN_ID, BOT_USERNAME, CHANNEL_ID, INSOLE_LENGTH_MAP,
                    PAYMENT_DETAILS, TELEGRAM_BOT_TOKEN)
from database import (add_product, get_all_products, get_products_by_size, get_product_by_id, init_db,
                      set_product_sold, update_message_id, update_product_price,
                      update_product_sizes,
                      delete_product_by_id)

active_reservations = {}

# Определяем состояния для диалога
PHOTO, SELECTING_SIZES, ENTERING_PRICE, AWAITING_PROOF, AWAITING_NAME, AWAITING_PHONE, AWAITING_CITY, AWAITING_DELIVERY_CHOICE, AWAITING_NP_DETAILS, AWAITING_UP_DETAILS = range(10)
SETTING_DETAILS = 10
ENTERING_NEW_PRICE = 11
EDITING_SIZES = 12
AWAITING_SIZE_SEARCH = 13


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

        # Фильтруем размеры, убирая забронированные
        reserved_for_this_product = active_reservations.get(product_id, set())
        available_sizes = [size for size in available_sizes if size not in reserved_for_this_product]

        # Если после фильтрации размеров не осталось
        if not available_sizes:
            await context.bot.send_message(
                chat_id=user_id,
                text="Вибачте, всі доступні розміри цього товару зараз заброньовані. Спробуйте пізніше."
            )
            return

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

    # Формируем словарь длин стелек на основе выбранных размеров
    insole_lengths = {
        size: INSOLE_LENGTH_MAP.get(size) for size in selected_sizes
    }
    insole_lengths_json = json.dumps(insole_lengths)

    # Добавляем товар в базу и получаем его ID
    product_id = add_product(
        file_id=file_id, price=price, sizes=selected_sizes,
        insole_lengths_json=insole_lengths_json
    )

    # Готовим пост для канала
    formatted_sizes = []
    for size in sorted(selected_sizes):
        length = insole_lengths.get(size)
        if length is not None:
            formatted_sizes.append(f"{size} ({length} см)")
        else:
            formatted_sizes.append(str(size))
    sizes_str = ", ".join(formatted_sizes)
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
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📝 Редагувати", callback_data=f"edit_{product['id']}"),
                    InlineKeyboardButton("🔁 Опублікувати", callback_data=f"repub_{product['id']}")
                ]
            ])
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
    print("--- ОТЛАДКА: Шаг 1/7 - Вход в payment_callback ---")

    user_id = update.effective_user.id
    # Извлекаем данные (формат: payment_{type}_{product_id}_{size})
    _, payment_type, product_id_str, selected_size = query.data.split('_')
    product_id = int(product_id_str)

    # Регистрируем бронь
    reservations_for_product = active_reservations.setdefault(product_id, set())
    reservations_for_product.add(selected_size)
    print(f"Новая бронь: {active_reservations}")

    # Шаг 1: Получаем товар и визуально убираем размер из поста в канале
    product = get_product_by_id(product_id)
    if not product or not product['message_id']:
        await query.message.reply_text("Вибачте, сталася помилка з товаром. Спробуйте пізніше.")
        return ConversationHandler.END

    # a. Получаем полный список размеров из БД
    all_db_sizes = set(product['sizes'].split(','))
    # b. Получаем все забронированные размеры для этого товара
    all_reserved_sizes = active_reservations.get(product_id, set())
    # c. Вычисляем новый список реально доступных размеров
    final_available_sizes = sorted([size for size in all_db_sizes if size not in all_reserved_sizes], key=int)

    print("--- ОТЛАДКА: Шаг 2/7 - Начало редактирования поста в канале ---")
    try:
        if final_available_sizes:
            new_sizes_str = ", ".join(final_available_sizes)
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
    print("--- ОТЛАДКА: Шаг 3/7 - Пост в канале отредактирован ---")

    print("--- ОТЛАДКА: Шаг 4/7 - Начало установки таймера ---")
    # Шаг 2: Запускаем таймер на 30 минут для отмены брони
    job = context.job_queue.run_once(
        cancel_reservation,
        1800,  # 30 минут
        data={'user_id': user_id, 'product_id': product_id, 'selected_size': selected_size},
        name=f"reservation_{user_id}_{product_id}"
    )
    print("--- ОТЛАДКА: Шаг 5/7 - Таймер установлен ---")

    # Шаг 3: Сохраняем данные для следующего шага
    context.user_data['reservation_job'] = job
    context.user_data['product_id'] = product_id
    context.user_data['selected_size'] = selected_size

    # Шаг 4: Информируем пользователя
    await query.edit_message_reply_markup(reply_markup=None)
    print("--- ОТЛАДКА: Шаг 6/7 - Отправка сообщения пользователю ---")
    await query.message.reply_text(
        f"Реквізити для оплати: {PAYMENT_DETAILS}\n"
        "Товар тимчасово заброньовано. У вас є 30 хвилин, щоб надіслати скріншот або файл, що підтверджує оплату. "
        "В іншому випадку бронь буде скасована, і товар знову стане доступним."
    )

    print("--- ОТЛАДКА: Шаг 7/7 - Выход из payment_callback ---")
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

    # Снимаем бронь из временного хранилища
    if product_id in active_reservations:
        active_reservations[product_id].discard(selected_size)
        # Если для этого товара больше нет броней, удаляем ключ
        if not active_reservations[product_id]:
            del active_reservations[product_id]
    print(f"Бронь снята по таймеру: {active_reservations}")

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
    """Сохраняет город и предлагает выбрать способ доставки."""
    context.user_data['city'] = update.message.text
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Нова Пошта", callback_data='delivery_np')],
        [InlineKeyboardButton("Укрпошта", callback_data='delivery_up')]
    ])
    await update.message.reply_text("Оберіть спосіб доставки:", reply_markup=keyboard)
    return AWAITING_DELIVERY_CHOICE


async def delivery_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор способа доставки."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'delivery_np':
        context.user_data['delivery_method'] = 'Нова Пошта'
        await query.edit_message_text("Введіть номер відділення або поштомату Нової Пошти.")
        return AWAITING_NP_DETAILS
    elif data == 'delivery_up':
        context.user_data['delivery_method'] = 'Укрпошта'
        await query.edit_message_text("Введіть Ваш поштовий індекс.")
        return AWAITING_UP_DETAILS


async def delivery_details_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Сохраняет детали доставки, собирает все данные, отправляет заказ менеджеру
    и завершает диалог.
    """
    # Сохраняем последнюю деталь (номер отделения или индекс)
    context.user_data['delivery_final_detail'] = update.message.text
    user_id = update.effective_user.id

    # 1. Собрать все данные
    user_data = context.user_data
    product_id = user_data.get('product_id')
    selected_size = user_data.get('selected_size')
    proof_file_id = user_data.get('proof_file_id')
    full_name = user_data.get('full_name')
    phone_number = user_data.get('phone_number')
    city = user_data.get('city')
    delivery_method = user_data.get('delivery_method')
    delivery_final_detail = user_data.get('delivery_final_detail')

    product = get_product_by_id(product_id)
    if not product:
        await update.message.reply_text(
            "Вибачте, сталася помилка з вашим замовленням. "
            "Будь ласка, зв'яжіться з менеджером напряму."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # 2. Сформировать "карточку заказа" для менеджера
    order_details = (
        f"🚨 <b>НОВЕ ЗАМОВЛЕННЯ</b> 🚨\n\n"
        f"<b>Товар ID:</b> {product_id}\n"
        f"<b>Обраний розмір:</b> {selected_size}\n"
        f"<b>Ціна:</b> {product['price']} грн\n\n"
        f"👤 <b>Клієнт:</b>\n"
        f"<b>ПІБ:</b> {full_name}\n"
        f"<b>Телефон:</b> {phone_number}\n"
        f"<b>Місто:</b> {city}\n"
    )
    if delivery_method == 'Нова Пошта':
        order_details += f"<b>Відділення/Поштомат НП:</b> {delivery_final_detail}"
    else:
        order_details += f"<b>Індекс Укрпошти:</b> {delivery_final_detail}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Підтвердити замовлення", callback_data=f"confirm_{product_id}_{selected_size}_{user_id}")]
    ])

    # 3. Отправить заказ менеджеру
    product_file_id = product['file_id']
    if product_file_id.startswith("BAAC"):
        await context.bot.send_video(chat_id=ADMIN_ID, video=product_file_id)
    else:
        await context.bot.send_photo(chat_id=ADMIN_ID, photo=product_file_id)

    await context.bot.send_photo(chat_id=ADMIN_ID, photo=proof_file_id, caption="Підтвердження оплати від клієнта")
    await context.bot.send_message(chat_id=ADMIN_ID, text=order_details, reply_markup=keyboard, parse_mode='HTML')

    await update.message.reply_text(
        "Дякуємо! Всі дані отримано. Ваше замовлення передається менеджеру на перевірку."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def confirm_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает подтверждение заказа менеджером: удаляет размер из БД,
    уведомляет клиента и обновляет сообщение для менеджера.
    """
    query = update.callback_query
    await query.answer()

    # 1. Извлечь данные
    try:
        _, product_id_str, selected_size, user_id_str = query.data.split('_')
        product_id = int(product_id_str)
        user_id = int(user_id_str)
    except (ValueError, IndexError) as e:
        print(f"Ошибка разбора callback_data в confirm_order_callback: {e}")
        await query.edit_message_text("Помилка: Некоректні дані в кнопці.")
        return

    # 2. Удалить размер из БД
    product = get_product_by_id(product_id)
    if not product:
        await query.edit_message_text(f"Помилка: Товар ID {product_id} не знайдено.")
        return

    current_sizes = product['sizes'].split(',')
    if selected_size in current_sizes:
        current_sizes.remove(selected_size)
        new_sizes_str = ",".join(sorted(current_sizes, key=int))
        update_product_sizes(product_id, new_sizes_str)

        # Также удаляем бронь из временного хранилища
        if product_id in active_reservations:
            active_reservations[product_id].discard(selected_size)
            if not active_reservations[product_id]:
                del active_reservations[product_id]
        print(f"Бронь снята после подтверждения: {active_reservations}")
    else:
        await query.answer("Цей розмір вже було продано або замовлення вже підтверджено.", show_alert=True)
        return

    # 3. Уведомить клиента
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="Ваше замовлення прийнято в обробку. Як тільки посилку буде відправлено, ми повідомимо вам номер ТТН."
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление клиенту {user_id}: {e}")

    # 4. Обновить сообщение для менеджера
    new_text = query.message.text + "\n\n<b>✅ ЗАМОВЛЕННЯ ПІДТВЕРДЖЕНО</b>"
    await query.edit_message_text(text=new_text, reply_markup=None, parse_mode='HTML')

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


async def edit_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие на кнопку 'Редагувати' и показывает меню редактирования."""
    query = update.callback_query
    await query.answer()

    try:
        product_id = int(query.data.split('_')[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Помилка: Некоректний ID товару.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Змінити ціну", callback_data=f"edit_price_{product_id}")],
        [InlineKeyboardButton("📏 Змінити розміри", callback_data=f"edit_sizes_{product_id}")],
        [InlineKeyboardButton("⬅️ Назад", callback_data=f"back_to_catalog_{product_id}")]
    ])

    await query.edit_message_reply_markup(reply_markup=keyboard)


async def back_to_catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие на кнопку 'Назад' и возвращает к исходной клавиатуре каталога."""
    query = update.callback_query
    await query.answer()

    try:
        product_id = int(query.data.split('_')[3])
    except (IndexError, ValueError):
        await query.edit_message_text("Помилка: Некоректний ID товару.")
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Редагувати", callback_data=f"edit_{product_id}"),
            InlineKeyboardButton("🔁 Опублікувати", callback_data=f"repub_{product_id}")
        ]
    ])

    await query.edit_message_reply_markup(reply_markup=keyboard)


async def edit_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог изменения цены."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[2])
    context.user_data['current_product_id'] = product_id
    context.user_data['message_to_edit_id'] = query.message.message_id

    await query.message.reply_text("Введіть нову ціну:")
    return ENTERING_NEW_PRICE


async def receive_new_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает новую цену, обновляет товар и исходное сообщение."""
    new_price_text = update.message.text
    if not new_price_text.isdigit():
        await update.message.reply_text("Будь ласка, введіть коректну ціну у вигляді числа.")
        return ENTERING_NEW_PRICE

    new_price = int(new_price_text)
    product_id = context.user_data.get('current_product_id')
    message_id = context.user_data.get('message_to_edit_id')
    chat_id = update.effective_chat.id

    if not product_id or not message_id:
        await update.message.reply_text("Сталася помилка, спробуйте знову.")
        return ConversationHandler.END

    update_product_price(product_id, new_price)
    product = get_product_by_id(product_id)

    new_caption = f"Ціна: {product['price']} грн.\nРозміри в наявності: {product['sizes']}"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📝 Редагувати", callback_data=f"edit_{product_id}"),
            InlineKeyboardButton("🔁 Опублікувати", callback_data=f"repub_{product_id}")
        ]
    ])

    await context.bot.edit_message_caption(
        chat_id=chat_id, message_id=message_id, caption=new_caption, reply_markup=keyboard
    )
    await update.message.reply_text("✅ Ціну успішно оновлено.")

    context.user_data.pop('current_product_id', None)
    context.user_data.pop('message_to_edit_id', None)
    return ConversationHandler.END


async def edit_sizes_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог изменения размеров товара."""
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.split('_')[2])
    product = get_product_by_id(product_id)

    if not product:
        await query.edit_message_text("Помилка: товар не знайдено.")
        return ConversationHandler.END

    current_sizes = [int(s) for s in product['sizes'].split(',') if s.isdigit()]

    context.user_data['current_product_id'] = product_id
    context.user_data['selected_sizes'] = current_sizes
    context.user_data['message_to_edit_id'] = query.message.message_id
    context.user_data['chat_id'] = query.message.chat_id

    keyboard = create_sizes_keyboard(current_sizes)
    await query.message.reply_text(
        "Оновіть список наявних розмірів:",
        reply_markup=keyboard
    )
    return EDITING_SIZES


async def edit_sizes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор размеров в режиме редактирования."""
    query = update.callback_query
    await query.answer()
    data = query.data

    selected_sizes = context.user_data.get('selected_sizes', [])

    if data == 'save':
        product_id = context.user_data.get('current_product_id')
        if not product_id:
            await query.message.reply_text("Сталася помилка, спробуйте знову.")
            return ConversationHandler.END

        new_sizes_str = ",".join(map(str, sorted(selected_sizes)))
        update_product_sizes(product_id, new_sizes_str)

        message_id = context.user_data.get('message_to_edit_id')
        chat_id = context.user_data.get('chat_id')
        product = get_product_by_id(product_id)

        new_caption = f"Ціна: {product['price']} грн.\nРозміри в наявності: {product['sizes']}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📝 Редагувати", callback_data=f"edit_{product_id}"),
            InlineKeyboardButton("🔁 Опублікувати", callback_data=f"repub_{product_id}")
        ]])

        await context.bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=new_caption, reply_markup=keyboard)
        await query.message.reply_text("✅ Розміри успішно оновлено.")

        for key in ['current_product_id', 'selected_sizes', 'message_to_edit_id', 'chat_id']:
            context.user_data.pop(key, None)
        return ConversationHandler.END
    elif data == 'undo':
        if selected_sizes: selected_sizes.pop()
    else:
        size = int(data)
        if size in selected_sizes: selected_sizes.remove(size)
        else: selected_sizes.append(size)
    keyboard = create_sizes_keyboard(selected_sizes)
    text = "Обрано: " + ", ".join(map(str, sorted(selected_sizes))) if selected_sizes else "Оберіть потрібні розміри:"
    await query.edit_message_text(text=text, reply_markup=keyboard)
    return EDITING_SIZES


async def find_size_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог поиска по размеру."""
    await update.message.reply_text("Введіть розмір для пошуку:")
    return AWAITING_SIZE_SEARCH


async def size_search_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает размер от пользователя, ищет товары в базе данных и отправляет результаты."""
    size_text = update.message.text
    if not size_text.isdigit():
        await update.message.reply_text("Будь ласка, введіть розмір коректно у вигляді числа.")
        return AWAITING_SIZE_SEARCH

    size = int(size_text)
    products = get_products_by_size(size)

    if not products:
        await update.message.reply_text("На жаль, за вашим запитом нічого не знайдено.")
        return ConversationHandler.END

    await update.message.reply_text("Ось що ми знайшли:")
    for product in products:
        caption = f"Ціна: {product['price']} грн."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]]
        )

        if product['file_id'].startswith("BAAC"):
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=product['file_id'], caption=caption, reply_markup=keyboard
            )
        else:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=product['file_id'], caption=caption, reply_markup=keyboard
            )
    return ConversationHandler.END


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


async def set_details_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог смены реквизитов."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Ця команда доступна лише адміністратору.")
        return ConversationHandler.END
    await update.message.reply_text("Надішліть новий текст з платіжними реквізитами.")
    return SETTING_DETAILS


async def receive_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает новые реквизиты и сохраняет их в config.py."""
    new_details = update.message.text
    config_path = 'config.py'

    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        for i, line in enumerate(lines):
            if line.strip().startswith('PAYMENT_DETAILS ='):
                lines[i] = f"PAYMENT_DETAILS = '{new_details}'\n"
                break

        with open(config_path, 'w', encoding='utf-8') as file:
            file.writelines(lines)

        await update.message.reply_text("✅ Реквізити успішно оновлено.")
    except Exception as e:
        print(f"Ошибка при обновлении реквизитов в config.py: {e}")
        await update.message.reply_text("Помилка! Не вдалося зберегти нові реквізити.")
    return ConversationHandler.END


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
            AWAITING_DELIVERY_CHOICE: [CallbackQueryHandler(delivery_choice_callback)],
            AWAITING_NP_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, delivery_details_received)],
            AWAITING_UP_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, delivery_details_received)],
        },
        fallbacks=[],
        allow_reentry=True
    )

    details_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('set_details', set_details_start)],
        states={
            SETTING_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_details)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    edit_price_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_price_start, pattern='^edit_price_')],
        states={
            ENTERING_NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_price)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    edit_sizes_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_sizes_start, pattern='^edit_sizes_')],
        states={
            EDITING_SIZES: [CallbackQueryHandler(edit_sizes_callback)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    find_size_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('findsize', find_size_start)],
        states={
            AWAITING_SIZE_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, size_search_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(details_conv_handler)
    application.add_handler(edit_price_conv_handler)
    application.add_handler(edit_sizes_conv_handler)
    application.add_handler(find_size_conv_handler)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(payment_conv_handler)
    application.add_handler(CommandHandler("catalog", show_catalog))
    application.add_handler(CommandHandler("delete", show_delete_list))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern='^del_'))
    application.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern='^confirm_del_'))
    application.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern='^cancel_del$'))
    application.add_handler(CallbackQueryHandler(republish_callback, pattern='^repub_'))
    application.add_handler(CallbackQueryHandler(edit_product_callback, pattern='^edit_'))
    application.add_handler(CallbackQueryHandler(back_to_catalog_callback, pattern='^back_to_catalog_'))
    application.add_handler(CallbackQueryHandler(size_callback, pattern='^ps_'))
    application.add_handler(CallbackQueryHandler(confirm_order_callback, pattern='^confirm_'))

    application.run_polling()


if __name__ == '__main__':
    main()