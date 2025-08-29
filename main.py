import asyncio
import json
import uuid
import re
import logging
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update,
                      InputMediaPhoto, InputMediaVideo)
from telegram.ext import (Application, CommandHandler, ContextTypes,
                          ConversationHandler, JobQueue, MessageHandler,
                          filters, CallbackQueryHandler)

from config import (ADMIN_IDS, BOT_USERNAME, CHANNEL_ID, INSOLE_LENGTH_MAP,
                    PAYMENT_DETAILS, TELEGRAM_BOT_TOKEN, ORDERS_CHANNEL_ID,
                    DISPATCH_CHANNEL_ID)
from database import (add_product, get_all_products, get_products_by_size, get_product_by_id, init_db,
                      set_product_sold, update_message_id, update_product_price,
                      update_product_sizes,
                      delete_product_by_id)

# Включаем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)


active_reservations = {}

# Определяем состояния для диалога
PHOTO, SELECTING_SIZES, ENTERING_PRICE, AWAITING_PROOF, AWAITING_NAME, AWAITING_PHONE, AWAITING_CITY, AWAITING_DELIVERY_CHOICE, AWAITING_NP_DETAILS, AWAITING_UP_DETAILS = range(10)
SETTING_DETAILS = 10
ENTERING_NEW_PRICE = 11
EDITING_SIZES = 12
AWAITING_SIZE_SEARCH = 13
WAITING_FOR_ACTION = 14


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обрабатывает команду /start.
    Если команда вызвана с параметром (deep link), запускает процесс покупки.
    Иначе, отправляет приветственное сообщение.
    """
    print(f"--- ОТЛАДКА: /start получил аргументы: {context.args} ---")
    args = context.args
    if args and args[0].startswith('buy_'):
        parts = args[0].split('_')
        user_id = update.effective_user.id

        # Новый формат: buy_{product_id}_{size}
        if len(parts) == 3:
            try:
                product_id = int(parts[1])
                selected_size = parts[2]
            except (IndexError, ValueError):
                await update.message.reply_text("Некоректне посилання для покупки.")
                return ConversationHandler.END

            product = get_product_by_id(product_id)
            if not product or not product['sizes']:
                await context.bot.send_message(chat_id=user_id, text="Вибачте, цей товар більше не доступний.")
                return ConversationHandler.END

            # Проверяем, доступен ли размер
            available_sizes_list = product['sizes'].split(',')
            reserved_for_this_product = active_reservations.get(product_id, [])
            if selected_size not in available_sizes_list or available_sizes_list.count(selected_size) <= reserved_for_this_product.count(selected_size):
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"Вибачте, розмір {selected_size} для цього товару більше не доступний або вже заброньований."
                )
                return ConversationHandler.END

            # Отправляем фото/видео товара
            file_id = product['file_id']
            if file_id.startswith("BAAC"):
                await context.bot.send_video(chat_id=user_id, video=file_id)
            else:
                await context.bot.send_photo(chat_id=user_id, photo=file_id)

            # Сразу предлагаем оплату (логика из size_callback)
            text = (f"Ви обрали розмір {selected_size}. Товар буде заброньовано для вас на 30 хвилин "
                    f"після отримання реквізитів.\n\nОберіть тип оплати:")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Передплата", callback_data=f"payment_prepay_{product_id}_{selected_size}")],
                [InlineKeyboardButton("Повна оплата", callback_data=f"payment_full_{product_id}_{selected_size}")]
            ])
            await context.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)

        # Старый формат: buy_{product_id}
        elif len(parts) == 2:
            try:
                product_id = int(parts[1])
            except (IndexError, ValueError):
                await update.message.reply_text("Некоректне посилання для покупки.")
                return ConversationHandler.END

            product = get_product_by_id(product_id)
            if not product or not product['sizes']:
                await context.bot.send_message(chat_id=user_id, text="Вибачте, цей товар більше не доступний.")
                return ConversationHandler.END

            # Отправляем фото/видео товара в личный чат
            file_id = product['file_id']
            if file_id.startswith("BAAC"):
                await context.bot.send_video(chat_id=user_id, video=file_id)
            else:
                await context.bot.send_photo(chat_id=user_id, photo=file_id)

            # Создаем клавиатуру с доступными размерами
            all_db_sizes = product['sizes'].split(',')
            reserved_sizes = active_reservations.get(product_id, [])
            available_sizes = list(all_db_sizes)
            for r_size in reserved_sizes:
                if r_size in available_sizes:
                    available_sizes.remove(r_size)

            if not available_sizes:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Вибачте, всі доступні розміри цього товару зараз заброньовані. Спробуйте пізніше."
                )
                return ConversationHandler.END

            keyboard_buttons = [InlineKeyboardButton(size, callback_data=f"ps_{product['id']}_{size}") for size in available_sizes]
            keyboard = [keyboard_buttons[i:i + 5] for i in range(0, len(keyboard_buttons), 5)]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(chat_id=user_id, text="Оберіть ваш розмір:", reply_markup=reply_markup)
        else:
            await update.message.reply_text("Некоректне посилання для покупки.")
        return ConversationHandler.END
    elif args and args[0] == 'find_size':
        await update.message.reply_text("Введіть розмір для пошуку:")
        return AWAITING_SIZE_SEARCH
    else:
        keyboard = [[InlineKeyboardButton("Пошук за розміром", callback_data='start_find_size')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Привіт! Я бот для продажу взуття.\n\n"
            "Натисніть кнопку, щоб знайти пару за вашим розміром.",
            reply_markup=reply_markup)
        return WAITING_FOR_ACTION

async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог добавления товара и запрашивает фото."""
    if update.effective_user.id not in ADMIN_IDS:
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
            formatted_sizes.append(f"<b>{size}</b> ({length} см)")
        else:
            formatted_sizes.append(f"<b>{size}</b>")
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
            chat_id=CHANNEL_ID, video=file_id, caption=caption, reply_markup=keyboard, parse_mode='HTML'
        )
    else:
        sent_message = await context.bot.send_photo(
            chat_id=CHANNEL_ID, photo=file_id, caption=caption, reply_markup=keyboard, parse_mode='HTML'
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

        is_admin = update.effective_user.id in ADMIN_IDS
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
    """Обрабатывает выбор размера и добавляет товар в корзину."""
    query = update.callback_query
    await query.answer()

    # Извлекаем данные из callback_data (формат: ps_{product_id}_{size})
    _, product_id_str, selected_size = query.data.split('_')
    product_id = int(product_id_str)

    # Создаем корзину, если ее нет
    if 'cart' not in context.user_data:
        context.user_data['cart'] = []

    # Добавляем товар в корзину
    context.user_data['cart'].append({'product_id': product_id, 'size': selected_size})

    text = f"✅ Розмір {selected_size} додано до вашого кошика."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Оформити замовлення", callback_data='checkout')],
        [InlineKeyboardButton("🛍️ Продовжити покупки", callback_data='continue_shopping')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard)


async def continue_shopping_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет сообщение с кнопками корзины, позволяя пользователю продолжить покупки."""
    query = update.callback_query
    await query.answer()
    await query.message.delete()


async def checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает сводку по корзине и предлагает перейти к оплате."""
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get('cart', [])
    if not cart:
        await query.edit_message_text("Ваш кошик порожній.")
        return

    summary_lines = []
    total_price = 0

    for item in cart:
        product_id = item['product_id']
        size = item['size']
        product = get_product_by_id(product_id)

        if product:
            # В базе нет названия, используем ID для идентификации
            product_name = f"Товар ID {product_id}"
            price = product['price']
            summary_lines.append(f"• {product_name}, розмір {size} - {price} грн")
            total_price += price
        else:
            summary_lines.append(f"• Невідомий товар (ID: {product_id}), розмір {size} - помилка")

    summary_text = "🛒 <b>Ваше замовлення:</b>\n\n" + "\n".join(summary_lines)
    summary_text += f"\n\n💰 <b>Загальна сума: {total_price} грн</b>"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Перейти до оплати", callback_data='proceed_to_payment')]
    ])

    await query.edit_message_text(text=summary_text, reply_markup=keyboard, parse_mode='HTML')


async def proceed_to_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Предлагает выбрать тип оплаты для всей корзины."""
    query = update.callback_query
    await query.answer()

    if not context.user_data.get('cart'):
        await query.edit_message_text("Ваш кошик порожній. Неможливо перейти до оплати.")
        return

    text = "Оберіть тип оплати:"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Передплата", callback_data='payment_cart_prepay')],
        [InlineKeyboardButton("Повна оплата", callback_data='payment_cart_full')]
    ])

    await query.edit_message_text(text=text, reply_markup=keyboard)


async def payment_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обрабатывает оплату для всей корзины, бронирует товары и запускает таймер.
    """
    query = update.callback_query
    await query.answer()
    print("\n--- [CART_DEBUG] Шаг 1: Вход в payment_cart_callback ---")

    user_id = update.effective_user.id
    cart = context.user_data.get('cart', [])
    if not cart:
        await query.edit_message_text("Ваш кошик порожній.")
        return ConversationHandler.END

    reserved_items = []
    # Предварительная проверка доступности всех товаров в корзине
    for item in cart:
        product_id = item['product_id']
        selected_size = item['size']
        product = get_product_by_id(product_id)
        if not product:
            await query.edit_message_text(f"Помилка: товар ID {product_id} не знайдено.")
            return ConversationHandler.END

        available_sizes_list = product['sizes'].split(',')
        reserved_for_this_product = active_reservations.get(product_id, [])

        # Считаем, сколько единиц этого размера уже в корзине
        num_in_cart = sum(1 for i in cart if i['product_id'] == product_id and i['size'] == selected_size)
        # Считаем, сколько доступно в БД с учетом уже существующих броней
        num_available_in_db = available_sizes_list.count(selected_size)
        num_already_reserved = reserved_for_this_product.count(selected_size)

        if num_in_cart > (num_available_in_db - num_already_reserved):
            await query.edit_message_text(f"Вибачте, товару ID {product_id} розміру {selected_size} недостатньо в наявності для вашого замовлення.")
            return ConversationHandler.END
    print("--- [CART_DEBUG] Шаг 2: Предварительная проверка наличия всех товаров пройдена ---")

    # Если все товары доступны, начинаем бронирование и обновление постов
    for item in cart:
        product_id = item['product_id']
        selected_size = item['size']
        print(f"--- [CART_DEBUG] Шаг 3: Бронирую товар {item['product_id']}, размер {item['size']} ---")

        # Регистрируем бронь
        active_reservations.setdefault(product_id, []).append(selected_size)
        reserved_items.append({'product_id': product_id, 'size': selected_size})

        # Обновляем пост в канале
        product = get_product_by_id(product_id)
        if not product or not product['message_id']:
            continue

        all_db_sizes_list = product['sizes'].split(',')
        all_reserved_sizes_list = active_reservations.get(product_id, [])
        final_available_sizes_list = list(all_db_sizes_list)
        for r_size in all_reserved_sizes_list:
            if r_size in final_available_sizes_list:
                final_available_sizes_list.remove(r_size)
        final_available_sizes = sorted(final_available_sizes_list, key=int)

        try:
            if final_available_sizes:
                insole_lengths = json.loads(product['insole_lengths_json']) if product['insole_lengths_json'] else {}
                formatted_sizes = [f"<b>{s}</b> ({insole_lengths.get(s)} см)" if insole_lengths.get(s) else f"<b>{s}</b>" for s in final_available_sizes]
                new_sizes_str = ", ".join(formatted_sizes)
                new_caption = (f"Натуральна шкіра\n{new_sizes_str} розмір\n{product['price']} грн наявність")
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]])
                await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=product['message_id'], caption=new_caption, reply_markup=keyboard, parse_mode='HTML')
            else:
                new_caption = (f"Натуральна шкіра\nПРОДАНО\n{product['price']} грн наявність")
                await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=product['message_id'], caption=new_caption, reply_markup=None)
        except Exception as e:
            print(f"Не удалось отредактировать сообщение в канале при бронировании корзины: {e}")
    print("--- [CART_DEBUG] Шаг 4: Все товары забронированы, посты в канале обновлены ---")

    # Определяем длительность брони и текст сообщения
    now = datetime.now()
    if 10 <= now.hour < 19:
        reservation_duration = 1800
        user_message = f"Реквізити для оплати:\n(натисніть на номер нижче, щоб скопіювати)\n<code>{PAYMENT_DETAILS}</code>\n\nТовари тимчасово заброньовано. У вас є 30 хвилин, щоб надіслати скріншот або файл, що підтверджує оплату. В іншому випадку бронь буде скасована, і товари знову стануть доступними для продажу."
    else:
        tomorrow = now.date() + timedelta(days=1)
        ten_am_tomorrow = datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=10)
        reservation_duration = (ten_am_tomorrow - now).total_seconds()
        user_message = f"Реквізити для оплати:\n(натисніть на номер нижче, щоб скопіювати)\n<code>{PAYMENT_DETAILS}</code>\n\nТовари тимчасово заброньовано до 10:00 ранку. Надішліть, будь ласка, скріншот або файл, що підтверджує оплату, до цього часу. В іншому випадку бронь буде скасована, і товари знову стануть доступними для продажу."

    job = context.job_queue.run_once(cancel_reservation, reservation_duration, data={'user_id': user_id, 'reserved_items': reserved_items}, name=f"reservation_cart_{user_id}")
    context.user_data['reservation_job'] = job
    context.user_data['cart_items_for_confirmation'] = reserved_items
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(user_message, parse_mode='HTML')
    print("--- [CART_DEBUG] Шаг 5: Таймер установлен, сообщение клиенту отправлено. Переход в состояние AWAITING_PROOF ---")
    return AWAITING_PROOF




async def cancel_reservation(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отменяет визуальную бронь, возвращая посту в канале исходное состояние. Работает как для одиночных товаров, так и для корзины.
    """
    job_data = context.job.data
    user_id = job_data['user_id']

    items_to_process = []
    is_cart_reservation = 'reserved_items' in job_data

    if is_cart_reservation:
        items_to_process.extend(job_data['reserved_items'])
        user_notification_text = "На жаль, час на оплату замовлення вичерпано. Ваша бронь скасовано. Товари знову доступні для покупки."
    else:  # Старая логика для одного товара
        items_to_process.append({'product_id': job_data['product_id'], 'selected_size': job_data['selected_size']})
        user_notification_text = f"На жаль, час на оплату товару (ID: {job_data['product_id']}, розмір: {job_data['selected_size']}) вичерпано. Ваша бронь скасовано. Товар знову доступний для покупки."

    updated_posts = set()

    for item in items_to_process:
        product_id = item['product_id']
        selected_size = item['selected_size']

        # Снимаем бронь из временного хранилища
        if product_id in active_reservations and selected_size in active_reservations.get(product_id, []):
            active_reservations[product_id].remove(selected_size)
            if not active_reservations[product_id]:
                del active_reservations[product_id]

        if product_id in updated_posts:
            continue

        product = get_product_by_id(product_id)
        if not product or not product['message_id']:
            print(f"Ошибка отмены брони: товар {product_id} или message_id не найден.")
            continue

        # Восстанавливаем подпись в посте канала, учитывая другие активные брони
        all_db_sizes_list = product['sizes'].split(',')
        all_current_reserved_sizes_list = active_reservations.get(product_id, [])

        final_available_sizes_list = list(all_db_sizes_list)
        for r_size in all_current_reserved_sizes_list:
            if r_size in final_available_sizes_list:
                final_available_sizes_list.remove(r_size)

        final_available_sizes = sorted(final_available_sizes_list, key=int)

        insole_lengths = json.loads(product['insole_lengths_json']) if product['insole_lengths_json'] else {}
        formatted_sizes = [f"<b>{s}</b> ({insole_lengths.get(s)} см)" if insole_lengths.get(s) else f"<b>{s}</b>" for s in final_available_sizes]
        new_sizes_str = ", ".join(formatted_sizes)
        new_caption = (f"Натуральна шкіра\n{new_sizes_str} розмір\n{product['price']} грн наявність")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}")]])

        try:
            await context.bot.edit_message_caption(
                chat_id=CHANNEL_ID, message_id=product['message_id'], caption=new_caption,
                reply_markup=keyboard, parse_mode='HTML'
            )
            updated_posts.add(product_id)
        except Exception as e:
            print(f"Не удалось обновить сообщение в канале при отмене брони: {e}")

    await context.bot.send_message(chat_id=user_id, text=user_notification_text)


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
    Сохраняет детали доставки, собирает все данные по корзине, отправляет заказ менеджеру
    и завершает диалог.
    """
    context.user_data['delivery_final_detail'] = update.message.text
    user_id = update.effective_user.id

    # 1. Собрать все данные по корзине и клиенту
    cart = context.user_data.get('cart_items_for_confirmation', [])
    if not cart:
        await update.message.reply_text("Помилка: ваш кошик порожній. Спробуйте почати спочатку.")
        return ConversationHandler.END

    user_data = context.user_data
    proof_file_id = user_data.get('proof_file_id')
    full_name = user_data.get('full_name')
    phone_number = user_data.get('phone_number')
    city = user_data.get('city')
    delivery_method = user_data.get('delivery_method')
    delivery_final_detail = user_data.get('delivery_final_detail')
    
    # 2. Сформировать "карточку заказа" для менеджера
    order_items_text_lines = []
    total_price = 0
    for item in cart:
        product = get_product_by_id(item['product_id'])
        if product:
            price = product['price']
            total_price += price
            order_items_text_lines.append(f"• Товар ID {item['product_id']}, розмір {item['size']} - {price} грн")
        else:
            order_items_text_lines.append(f"• Товар ID {item['product_id']}, розмір {item['size']} - НЕ ЗНАЙДЕНО")

    order_items_text = "\n".join(order_items_text_lines)

    order_details = (
        f"🚨 <b>НОВЕ ЗАМОВЛЕННЯ</b> 🚨\n\n"
        f"<b>Склад замовлення:</b>\n{order_items_text}\n\n"
        f"<b>Загальна сума:</b> {total_price} грн\n\n"
        f"👤 <b>Клієнт:</b>\n"
        f"<b>ПІБ:</b> {full_name}\n"
        f"<b>Телефон:</b> {phone_number}\n"
        f"<b>Місто:</b> {city}\n"
    )
    if delivery_method == 'Нова Пошта':
        order_details += f"<b>Відділення/Поштомат НП:</b> {delivery_final_detail}"
    else:
        order_details += f"<b>Індекс Укрпошти:</b> {delivery_final_detail}"

    # Генерируем уникальный ID для заказа и сохраняем корзину
    order_id = str(uuid.uuid4())
    context.bot_data[order_id] = cart

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Підтвердити замовлення", callback_data=f"confirm_cart_{order_id}_{user_id}")]
    ])

    # 3. Отправить заказ менеджеру
    # Сначала все фото/видео товаров
    for item in cart:
        product = get_product_by_id(item['product_id'])
        if product:
            product_file_id = product['file_id']
            if product_file_id.startswith("BAAC"):
                await context.bot.send_video(chat_id=ORDERS_CHANNEL_ID, video=product_file_id)
            else:
                await context.bot.send_photo(chat_id=ORDERS_CHANNEL_ID, photo=product_file_id)

    # Затем подтверждение оплаты и детали заказа
    await context.bot.send_photo(chat_id=ORDERS_CHANNEL_ID, photo=proof_file_id, caption="Підтвердження оплати від клієнта")
    await context.bot.send_message(chat_id=ORDERS_CHANNEL_ID, text=order_details, reply_markup=keyboard, parse_mode='HTML')

    await update.message.reply_text(
        "Дякуємо! Всі дані отримано. Ваше замовлення передається менеджеру на перевірку."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def confirm_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает подтверждение заказа по корзине: удаляет размеры из БД,
    уведомляет клиента и обновляет сообщение для менеджера.
    """
    query = update.callback_query
    await query.answer()

    # 1. Извлечь данные (формат: confirm_cart_{order_id}_{user_id})
    try:
        print("\n--- [CONFIRM_DEBUG] Шаг 1: Вход в confirm_order_callback ---")
        _, _, order_id, user_id_str = query.data.split('_')
        user_id = int(user_id_str)
        print(f"--- [CONFIRM_DEBUG] Шаг 2: Разобраны данные: order_id={order_id}, user_id={user_id} ---")
    except (ValueError, IndexError) as e:
        print(f"Ошибка разбора callback_data в confirm_order_callback: {e}")
        await query.edit_message_text("Помилка: Некоректні дані в кнопці.")
        return

    # 2. Извлечь корзину из bot_data
    cart = context.bot_data.pop(order_id, [])
    print(f"--- [CONFIRM_DEBUG] Шаг 3: Извлечена корзина: {cart} ---")
    if not cart:
        await query.answer("Це замовлення вже було оброблено або не знайдено.", show_alert=True)
        # Обновляем сообщение, чтобы убрать кнопку и показать, что обработано
        new_text = query.message.text + "\n\n<b>⚠️ ЗАМОВЛЕННЯ ВЖЕ ОБРОБЛЕНО</b>"
        await query.edit_message_text(text=new_text, reply_markup=None, parse_mode='HTML')
        return

    # 3. Обработать каждый товар в корзине
    for item in cart:
        print(f"--- [CONFIRM_DEBUG] Шаг 4: Обрабатываю товар {item} ---")
        product_id = item['product_id']
        selected_size = item['size']

        # Удаляем размер из БД
        product = get_product_by_id(product_id)
        if product:
            current_sizes = product['sizes'].split(',')
            if selected_size in current_sizes:
                current_sizes.remove(selected_size)
                new_sizes_str = ",".join(sorted(current_sizes, key=int))
                update_product_sizes(product_id, new_sizes_str)
            else:
                print(f"Предупреждение: Размер {selected_size} для товара {product_id} не найден в БД при подтверждении заказа.")

        # Снимаем бронь из временного хранилища
        if product_id in active_reservations and selected_size in active_reservations.get(product_id, []):
            active_reservations[product_id].remove(selected_size)
            if not active_reservations[product_id]:
                del active_reservations[product_id]
    print(f"Брони сняты после подтверждения заказа {order_id}: {active_reservations}")

    # 4. Уведомить клиента
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="Ваше замовлення прийнято в обробку. Як тільки посилку буде відправлено, ми повідомимо вам номер ТТН."
        )
    except Exception as e:
        print(f"Не удалось отправить уведомление клиенту {user_id}: {e}")

    # 5. Пересылаем подтвержденный заказ в канал для отправок
    print("--- [CONFIRM_DEBUG] Шаг 5: Готовлюсь к отправке в канал 'Отправки' ---")
    try:
        # Сначала отправляем фото/видео каждого товара
        for item in cart:
            product = get_product_by_id(item['product_id'])
            if product:
                product_file_id = product['file_id']
                if product_file_id.startswith("BAAC"):
                    await context.bot.send_video(chat_id=DISPATCH_CHANNEL_ID, video=product_file_id)
                else:
                    await context.bot.send_photo(chat_id=DISPATCH_CHANNEL_ID, photo=product_file_id)
            else:
                print(f"--- [CONFIRM_DEBUG] Товар {item['product_id']} не найден в БД для отправки фото в канал 'Отправки'")

        original_order_text = query.message.text
        dispatch_text = (
            f"\n\n---\n\n🚚 <b>ЗАМОВЛЕННЯ ПЕРЕДАНО НА ВІДПРАВКУ</b>\n\n"
            f"{original_order_text}\n\n"
            f"<b>ID Клієнта для ТТН:</b> <code>{user_id}</code>"
        )
        await context.bot.send_message(chat_id=DISPATCH_CHANNEL_ID, text=dispatch_text, parse_mode='HTML')
    except Exception as e:
        print(f"Не удалось отправить заказ в канал для отправок: {e}")
        print(f"--- [CONFIRM_DEBUG] ОШИБКА при отправке в канал 'Отправки': {e} ---")

    # 6. Обновить сообщение для менеджера
    new_text = query.message.text + "\n\n<b>✅ ЗАМОВЛЕННЯ ПІДТВЕРДЖЕНО</b>"
    await query.edit_message_text(text=new_text, reply_markup=None, parse_mode='HTML')


async def handle_ttn_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает ответ менеджера с ТТН в канале отправок.
    Извлекает ID клиента, отправляет ему ТТН и обновляет сообщение в канале.
    """
    try:
        print("\n--- [TTN_DEBUG] Шаг 1: Вход в handle_ttn_reply ---")
        # 1. Получаем ТТН и исходное сообщение
        ttn_number = update.channel_post.text
        original_message = update.channel_post.reply_to_message

        if not original_message or not original_message.text:
            # Менеджер ответил не на сообщение или на сообщение без текста
            return

        # 2. Извлекаем ID клиента из текста исходного сообщения
        match = re.search(r"ID Клієнта для ТТН: (\d+)", original_message.text)
        if not match:
            # Не удалось найти ID клиента в сообщении
            print("Не удалось извлечь ID клиента из сообщения для отправки ТТН.")
            return

        user_id = int(match.group(1))
        print(f"--- [TTN_DEBUG] Шаг 2: Извлечен user_id: {user_id}. Текст ТТН: {ttn_number} ---")

        # 3. Отправляем ТТН клиенту
        print(f"--- [TTN_DEBUG] Шаг 3: Пытаюсь отправить сообщение клиенту {user_id} ---")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Ваше замовлення відправлено! Номер ТТН: {ttn_number}"
        )
        print("--- [TTN_DEBUG] Шаг 4: Сообщение клиенту успешно отправлено ---")

        # Шаг 1: Извлечение данных для кнопок
        product_id_match = re.search(r"Товар ID: (\d+)", original_message.text)
        size_match = re.search(r"Обраний розмір: (\d+)", original_message.text)

        keyboard = None
        if product_id_match and size_match:
            product_id = product_id_match.group(1)
            size = size_match.group(1)
            # Шаг 2: Создание кнопок
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Забрали", callback_data=f"status_picked_{product_id}_{size}"),
                    InlineKeyboardButton("↩️ Відмова", callback_data=f"status_returned_{product_id}_{size}")
                ]
            ])

        # 4. Редактируем исходное сообщение в канале
        new_text = original_message.text + "\n\n✅ <b>ТТН ВІДПРАВЛЕНО КЛІЄНТУ</b>"
        await original_message.edit_text(text=new_text, reply_markup=keyboard, parse_mode='HTML')

    except Exception as e:
        print(f"Ошибка в handle_ttn_reply: {e}")


async def handle_order_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает нажатия на кнопки статуса заказа ("Забрали" или "Відмова").
    """
    query = update.callback_query
    await query.answer()

    try:
        _, status, product_id_str, size = query.data.split('_')
        product_id = int(product_id_str)

        final_text_addition = ""
        if status == 'picked':
            final_text_addition = "\n\n✅ <b>ЗАМОВЛЕННЯ УСПІШНО ЗАВЕРШЕНО</b>"

        elif status == 'returned':
            product = get_product_by_id(product_id)
            if not product:
                await query.edit_message_text("Помилка: товар не знайдено в базі.", reply_markup=None)
                return

            current_sizes = product['sizes'].split(',') if product['sizes'] else []
            current_sizes.append(size)
            new_sizes_str = ",".join(sorted(current_sizes, key=int))
            update_product_sizes(product_id, new_sizes_str)

            # Обновляем пост в основном канале, чтобы показать, что размер снова в наличии
            updated_product = get_product_by_id(product_id)
            if updated_product and updated_product['message_id']:
                try:
                    # Формируем новую подпись
                    all_sizes = sorted(updated_product['sizes'].split(','), key=int)
                    insole_lengths = json.loads(updated_product['insole_lengths_json']) if updated_product['insole_lengths_json'] else {}

                    formatted_sizes = []
                    for s in all_sizes:
                        length = insole_lengths.get(s)
                        if length is not None:
                            formatted_sizes.append(f"<b>{s}</b> ({length} см)")
                        else:
                            formatted_sizes.append(f"<b>{s}</b>")

                    sizes_str = ", ".join(formatted_sizes)
                    new_caption = (f"Натуральна шкіра\n"
                                   f"{sizes_str} розмір\n"
                                   f"{updated_product['price']} грн наявність")

                    keyboard = InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product_id}")]]
                    )

                    await context.bot.edit_message_caption(
                        chat_id=CHANNEL_ID, message_id=updated_product['message_id'], caption=new_caption, reply_markup=keyboard, parse_mode='HTML'
                    )
                except Exception as e:
                    print(f"Не удалось обновить пост в основном канале при возврате: {e}")

            final_text_addition = "\n\n↩️ <b>ВІДМОВА. ТОВАР ПОВЕРНЕНО В БАЗУ ДАНИХ</b>"

        if final_text_addition:
            new_text = query.message.text + final_text_addition
            await query.edit_message_text(text=new_text, reply_markup=None, parse_mode='HTML')
            
    except Exception as e:
        print(f"Помилка в handle_order_status_callback: {e}")
        await query.message.reply_text("Сталася помилка при обробці статусу.")

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
        original_sizes = sorted(product['sizes'].split(','), key=int)
        insole_lengths = json.loads(product['insole_lengths_json']) if product['insole_lengths_json'] else {}

        formatted_sizes = []
        for size in original_sizes:
            length = insole_lengths.get(size)
            if length is not None:
                formatted_sizes.append(f"<b>{size}</b> ({length} см)")
            else:
                formatted_sizes.append(f"<b>{size}</b>")
        sizes_str = ", ".join(formatted_sizes)
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
            sent_message = await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id, caption=caption,
                                                        reply_markup=keyboard, parse_mode='HTML')
        else:
            sent_message = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=caption,
                                                        reply_markup=keyboard, parse_mode='HTML')
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
        # Всегда добавляем размер, удаление только по кнопке "undo"
        selected_sizes.append(int(data))
    keyboard = create_sizes_keyboard(selected_sizes)
    text = "Обрано: " + ", ".join(map(str, sorted(selected_sizes))) if selected_sizes else "Оберіть потрібні розміри:"
    await query.edit_message_text(text=text, reply_markup=keyboard)
    return EDITING_SIZES


async def find_size_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает диалог поиска по размеру."""
    query = update.callback_query
    text = "Введіть розмір для пошуку:"
    if query:
        await query.answer()
        user_id = query.from_user.id
        await context.bot.send_message(chat_id=user_id, text=text)
    else:
        await update.message.reply_text(text)
    return AWAITING_SIZE_SEARCH


async def display_search_page(update: Update, context: ContextTypes.DEFAULT_TYPE, size: int, page: int):
    """Отображает страницу результатов поиска с галереей и клавиатурой."""
    all_products = get_products_by_size(size)
    all_products = [
        p for p in all_products if p['sizes'].split(',').count(str(size)) > active_reservations.get(p['id'], []).count(str(size))
    ]

    chat_id = update.effective_chat.id

    if not all_products and page == 1:
        await context.bot.send_message(chat_id=chat_id, text="На жаль, за вашим запитом нічого не знайдено.")
        return

    page_size = 9
    start_index = (page - 1) * page_size
    end_index = page * page_size
    products_on_page = all_products[start_index:end_index]

    if not products_on_page:
        query = update.callback_query
        if query:
            await query.answer("Більше товарів не знайдено.", show_alert=True)
        return

    # Отправка галереи
    media_group = []
    for i, product in enumerate(products_on_page):
        caption = "Ось що ми знайшли:" if i == 0 and page == 1 else None
        file_id = product['file_id']
        if file_id.startswith("BAAC"):
            media_group.append(InputMediaVideo(media=file_id, caption=caption))
        else:
            media_group.append(InputMediaPhoto(media=file_id, caption=caption))

    if media_group:
        await context.bot.send_media_group(chat_id=chat_id, media=media_group)

    # Отправка клавиатуры
    keyboard_rows = []
    for product in products_on_page:
        length_text_part = ""
        if product['insole_lengths_json']:
            try:
                insole_lengths = json.loads(product['insole_lengths_json'])
                length = insole_lengths.get(str(size))
                if length is not None:
                    length_text_part = f" ({length} см)"
            except (json.JSONDecodeError, TypeError):
                pass

        button_text = f"{size}{length_text_part}-{product['price']}грн"
        callback_data = f"gallery_select_{product['id']}_{size}"
        keyboard_rows.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    nav_buttons = []
    if start_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"search_page_{page - 1}_{size}"))
    if end_index < len(all_products):
        nav_buttons.append(InlineKeyboardButton("Далі ➡️", callback_data=f"search_page_{page + 1}_{size}"))

    if nav_buttons:
        keyboard_rows.append(nav_buttons)

    if keyboard_rows:
        reply_markup = InlineKeyboardMarkup(keyboard_rows)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Оберіть товар:",
            reply_markup=reply_markup
        )


async def size_search_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает поиск по размеру и отображает первую страницу результатов."""
    size_text = update.message.text
    if not size_text.isdigit():
        await update.message.reply_text("Будь ласка, введіть розмір коректно у вигляді числа.")
        return AWAITING_SIZE_SEARCH

    size = int(size_text)
    await display_search_page(update, context, size=size, page=1)
    return ConversationHandler.END


async def search_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает переключение страниц в результатах поиска."""
    query = update.callback_query
    await query.answer()

    try:
        _, _, page_str, size_str = query.data.split('_')
        page = int(page_str)
        size = int(size_str)
    except (ValueError, IndexError):
        await query.message.reply_text("Помилка: некоректні дані для пагінації.")
        return

    # Отображаем новую страницу
    await display_search_page(update, context, size=size, page=page)


async def gallery_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает выбор товара из галереи и показывает его детально."""
    query = update.callback_query
    await query.answer()

    try:
        _, _, product_id_str, size = query.data.split('_')
        product_id = int(product_id_str)
    except (IndexError, ValueError):
        await query.message.reply_text("Помилка: Некоректний ID товару.")
        return

    product = get_product_by_id(product_id)
    if not product or not product['sizes']:
        await query.message.reply_text("Вибачте, цей товар більше не доступний.")
        return

    sizes_str = ", ".join(sorted(product['sizes'].split(','), key=int))
    caption = f"Ціна: {product['price']} грн.\nРозміри в наявності: {sizes_str}"

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🛒 Купити", url=f"https://t.me/{BOT_USERNAME}?start=buy_{product['id']}_{size}")]]
    )

    file_id = product['file_id']
    if file_id.startswith("BAAC"):
        await context.bot.send_video(chat_id=query.message.chat.id, video=file_id, caption=caption, reply_markup=keyboard)
    else:
        await context.bot.send_photo(chat_id=query.message.chat.id, photo=file_id, caption=caption, reply_markup=keyboard)


async def show_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выводит список товаров для удаления."""
    if update.effective_user.id not in ADMIN_IDS:
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
    if update.effective_user.id not in ADMIN_IDS:
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


async def test_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет тестовую кнопку для отладки deep link."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда доступна лише адміністратору.")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Тестова кнопка", url=f'https://t.me/{BOT_USERNAME}?start=find_size')]
    ])
    await update.message.reply_text('Це тестова кнопка:', reply_markup=keyboard)


async def create_find_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Публикует в канале пост с кнопкой для поиска по размеру."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда доступна лише адміністратору.")
        return

    text = "Для пошуку взуття за розміром, натисніть кнопку справа 👉"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пошук за розміром", url=f'https://t.me/{BOT_USERNAME}?start=find_size')]
    ])

    try:
        sent_message = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=keyboard)
        # Закрепляем сообщение в канале с уведомлением для подписчиков
        await context.bot.pin_chat_message(chat_id=CHANNEL_ID, message_id=sent_message.message_id, disable_notification=False)
        await update.message.reply_text("✅ Пост с кнопкой поиска успешно опубликован и закреплен в канале.")
    except Exception as e:
        await update.message.reply_text(f"Не вдалося опублікувати та закріпити пост. Помилка: {e}")


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
        entry_points=[
            
            CallbackQueryHandler(payment_cart_callback, pattern='^payment_cart_')
        ],
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
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('findsize', find_size_start),
            CallbackQueryHandler(find_size_start, pattern='^start_find_size$')
        ],
        states={
            WAITING_FOR_ACTION: [CallbackQueryHandler(find_size_start, pattern='^start_find_size$')],
            AWAITING_SIZE_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, size_search_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_chat=False,
    )

    application.add_handler(details_conv_handler)
    application.add_handler(edit_price_conv_handler)
    application.add_handler(edit_sizes_conv_handler)
    application.add_handler(find_size_conv_handler)

    application.add_handler(conv_handler)
    application.add_handler(payment_conv_handler)
    application.add_handler(CommandHandler("catalog", show_catalog))
    application.add_handler(CommandHandler("delete", show_delete_list))
    application.add_handler(CommandHandler("testbutton", test_button))
    application.add_handler(CommandHandler('createbuttonpost', create_find_post))
    application.add_handler(CallbackQueryHandler(delete_callback, pattern='^del_'))
    application.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern='^confirm_del_'))
    application.add_handler(CallbackQueryHandler(cancel_delete_callback, pattern='^cancel_del$'))
    application.add_handler(CallbackQueryHandler(republish_callback, pattern='^repub_'))
    application.add_handler(CallbackQueryHandler(edit_product_callback, pattern='^edit_'))
    application.add_handler(CallbackQueryHandler(back_to_catalog_callback, pattern='^back_to_catalog_'))
    application.add_handler(CallbackQueryHandler(size_callback, pattern='^ps_'))
    application.add_handler(CallbackQueryHandler(continue_shopping_callback, pattern='^continue_shopping$'))
    application.add_handler(CallbackQueryHandler(checkout_callback, pattern='^checkout$'))
    application.add_handler(CallbackQueryHandler(proceed_to_payment_callback, pattern='^proceed_to_payment$'))
    application.add_handler(CallbackQueryHandler(confirm_order_callback, pattern='^confirm_cart_'))
    application.add_handler(CallbackQueryHandler(search_page_callback, pattern='^search_page_'))
    application.add_handler(CallbackQueryHandler(gallery_select_callback, pattern='^gallery_select_'))
    application.add_handler(CallbackQueryHandler(handle_order_status_callback, pattern='^status_'))
    # Обработчик для отправки ТТН
    application.add_handler(MessageHandler(filters.REPLY & filters.Chat(chat_id=DISPATCH_CHANNEL_ID), handle_ttn_reply))

    application.run_polling()


if __name__ == '__main__':
    main()