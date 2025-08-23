import sqlite3


def init_db():
    """
    Инициализирует базу данных: подключается к shop.db и создает таблицу products.
    """
    conn = sqlite3.connect('shop.db')
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

    conn.commit()
    conn.close()


def add_product(file_id: str, price: int, sizes: list[int], insole_lengths_json: str):
    """
    Добавляет новый товар в базу данных и возвращает его ID.
    """
    conn = sqlite3.connect('shop.db')
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
    conn = sqlite3.connect('shop.db')
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
    conn = sqlite3.connect('shop.db')
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
    conn = sqlite3.connect('shop.db')
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
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET message_id = ? WHERE id = ?", (message_id, product_id))
    conn.commit()
    conn.close()


def update_product_sizes(product_id: int, new_sizes: str):
    """
    Обновляет список доступных размеров для товара.
    """
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET sizes = ? WHERE id = ?", (new_sizes, product_id))
    if not new_sizes:
        cursor.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def update_product_price(product_id: int, new_price: int):
    """
    Обновляет цену для указанного товара.
    """
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET price = ? WHERE id = ?", (new_price, product_id))
    conn.commit()
    conn.close()


def set_product_sold(product_id: int):
    """
    Устанавливает для товара статус 'продано'.
    """
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def delete_product_by_id(product_id: int):
    """
    Удаляет товар из базы данных по его ID.
    """
    conn = sqlite3.connect('shop.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()