import sqlite3


def init_db():
    """
    Инициализирует базу данных: подключается к shop.db и создает таблицу products.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            sizes TEXT NOT NULL,
            is_sold INTEGER DEFAULT 0,
            message_id INTEGER
        )
    ''')

    # Проверяем, существует ли колонка insole_lengths_json
    cursor.execute("PRAGMA table_info(products)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'insole_lengths_json' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN insole_lengths_json TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY,
            keywords TEXT NOT NULL,
            answer TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_chats (
            user_id INTEGER PRIMARY KEY,
            admin_id INTEGER,
            status TEXT NOT NULL,
            last_update TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()


def add_product(file_id: str, price: int, sizes: list[int], insole_lengths_json: str):
    """
    Добавляет новый товар в базу данных и возвращает его ID.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()

    sizes_str = ",".join(map(str, sorted(sizes)))
    cursor.execute("INSERT INTO products (file_id, price, sizes, insole_lengths_json) VALUES (?, ?, ?, ?)",
                   (file_id, price, sizes_str, insole_lengths_json))
    product_id = cursor.lastrowid

    conn.commit()
    conn.close()
    return product_id


def get_all_products():
    """
    Возвращает список всех товаров, которые не проданы.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row  # Позволяет обращаться к колонкам по имени
    cursor = conn.cursor()
    cursor.execute("SELECT id, file_id, price, sizes FROM products WHERE is_sold = 0")
    products = cursor.fetchall()
    conn.close()
    return products


def get_products_by_size(size):
    """
    Возвращает список всех товаров, которые не проданы и доступны в указанном размере.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    size_str = str(size)
    # Ищем точное совпадение, или в начале, или в конце, или в середине списка
    cursor.execute("""
        SELECT * FROM products
        WHERE is_sold = 0 AND (
            sizes = ? OR
            sizes LIKE ? OR
            sizes LIKE ? OR
            sizes LIKE ?
        )
    """, (size_str, f"{size_str},%", f"%,{size_str}", f"%,{size_str},%"))
    products = cursor.fetchall()
    conn.close()
    return products


def get_product_by_id(product_id: int):
    """
    Возвращает информацию о товаре по его ID.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    product = cursor.fetchone()
    conn.close()
    return product


def update_message_id(product_id: int, message_id: int):
    """
    Обновляет message_id для указанного товара.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET message_id = ? WHERE id = ?", (message_id, product_id))
    conn.commit()
    conn.close()


def update_product_sizes(product_id, new_sizes):
    """
    Обновляет список доступных размеров для товара и флаг is_sold.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET sizes = ? WHERE id = ?", (new_sizes, product_id))
    if not new_sizes:
        # Если размеры закончились, помечаем товар как проданный
        cursor.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (product_id,))
    else:
        # Если размеры есть, помечаем товар как не проданный
        cursor.execute("UPDATE products SET is_sold = 0 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def update_product_price(product_id: int, new_price: int):
    """
    Обновляет цену для указанного товара.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, product_id))
    conn.commit()
    conn.close()


def set_product_sold(product_id: int):
    """
    Устанавливает для товара статус 'продано'.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def delete_product_by_id(product_id: int):
    """
    Удаляет товар из базы данных по его ID.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def add_faq(keywords: str, answer: str) -> int:
    """
    Добавляет новую запись в таблицу FAQ и возвращает ее ID.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO faq (keywords, answer) VALUES (?, ?)", (keywords, answer))
    faq_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return faq_id


def delete_faq_by_id(faq_id: int):
    """
    Удаляет запись из таблицы FAQ по ее ID.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM faq WHERE id = ?", (faq_id,))
    conn.commit()
    conn.close()


def find_faq_by_keywords(user_message: str) -> str | None:
    """
    Ищет ответ в FAQ по ключевым словам в сообщении пользователя.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT keywords, answer FROM faq")
    all_faqs = cursor.fetchall()
    conn.close()

    lower_user_message = user_message.lower()

    for faq_item in all_faqs:
        keywords = [kw.strip().lower() for kw in faq_item['keywords'].split(',')]
        if any(keyword in lower_user_message for keyword in keywords if keyword):
            return faq_item['answer']

    return None


def get_all_faq() -> list:
    """
    Возвращает список всех записей из таблицы FAQ.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, keywords, answer FROM faq")
    all_faqs = cursor.fetchall()
    conn.close()
    return all_faqs


def set_chat_status(user_id: int, status: str, admin_id: int = None):
    """
    Создает или обновляет запись о чате для конкретного пользователя.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO live_chats (user_id, status, admin_id, last_update) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
        (user_id, status, admin_id)
    )
    conn.commit()
    conn.close()


def get_chat_by_user_id(user_id: int):
    """
    Получает информацию о чате по user_id.
    """
    conn = sqlite3.connect('shoes_bot.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM live_chats WHERE user_id = ?", (user_id,))
    chat_info = cursor.fetchone()
    conn.close()
    return chat_info


def delete_chat(user_id: int):
    """
    Удаляет запись о чате по user_id.
    """
    conn = sqlite3.connect('shoes_bot.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM live_chats WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()