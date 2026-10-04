import sqlite3
import random
import string
from datetime import datetime

DB_ID = "openbot_id.db"

ROLE = {
    "developer": "🌐 Developer",
    "owner": "👑 Владелец",
    "admin": "⭐ Администратор",
    "moderator": "👨‍💼 Модератор",
    "tester": "🧪 Тестер",
    "user": "👤 Игрок",
    "frozen": "❄️ Заморожен",
    "banned": "☠️ Забанен",
    "superuser": "⚡ Суперпользователь"
}

LEVEL = {1: "1 ур.", 2: "2 ур.", 3: "3 ур.", 4: "4 ур."}

def init_id_system():
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER PRIMARY KEY,
                active_bots TEXT DEFAULT '',
                name TEXT,
                emoji TEXT DEFAULT NULL,
                old_emoji TEXT DEFAULT NULL,
                player_tag TEXT UNIQUE,
                global_status TEXT DEFAULT 'user',
                created_at TEXT,
                bio TEXT DEFAULT NULL,
                is_public INTEGER DEFAULT 1,
                level INTEGER DEFAULT 1
            )
        """)
        conn.commit()

# --- Базовые функции БД ---
def get_id(user_id):
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE user_id = ?", (user_id,))
        return cursor.fetchone()

def is_dev_mode_enabled(user_id: int) -> bool:
    data = get_id(user_id)
    return bool(data[11]) if data else False

def is_dev_mode(user_id: int) -> bool:
    return is_dev_mode_enabled(user_id)

def check_dev_tester_level(user_id: int, required_level: int = 1) -> bool:
    """
    Проверяет, имеет ли пользователь статус developer или tester
    и соответствует ли его уровень требуемому уровню.

    Developer с нужным уровнем -> True
    Tester с нужным уровнем -> True
    Остальные статусы -> False
    """

    data = get_id(user_id)

    if not data:
        return False

    status = str(data[6]).strip().lower()
    level = data[10] if data[10] is not None else 1

    # Разрешаем только Developer и Tester
    if status not in ("developer", "tester"):
        return False

    # Проверяем уровень
    return level >= required_level

def toggle_dev_mode(user_id: int) -> bool:
    current = is_dev_mode_enabled(user_id)
    new_state = 0 if current else 1
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET dev_mode = ? WHERE user_id = ?", (new_state, user_id))
        conn.commit()
    return bool(new_state)

def get_status(user_id: int) -> str:
    data = get_id(user_id)
    return str(data[6]).strip().lower() if data and data[6] else "user"

def is_dev_or_owner(user_id: int) -> bool:
    base_status = get_status(user_id)
    if base_status in ["developer", "owner"]:
        return True
    if base_status == "superuser" and is_dev_mode_enabled(user_id):
        return True
    return False

def is_globally_banned(user_id):
    data = get_id(user_id)
    return data and data[6] in ("banned", "frozen")

def get_developers_count() -> int:
    """Возвращает количество разработчиков из openbot_id"""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM accounts WHERE global_status IN ('developer', 'owner')")
        result = cursor.fetchone()
        return result[0] if result else 0

# --- Дополнительные функции для совместимости ---
def get_active_bots(user_id):
    data = get_id(user_id)
    return data[1].split(',') if data and data[1] else []

def register_bot_activity(user_id, bot_name):
    pass

def create_id(user_id, name, emoji=None):
    tag = "#" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    date = datetime.now().strftime("%d.%m.%Y")
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO accounts (user_id, name, emoji, player_tag, created_at) VALUES (?, ?, ?, ?, ?)",
                       (user_id, name, emoji, tag, date))
        conn.commit()
    return tag

def update_bio(user_id, bio):
    with sqlite3.connect(DB_ID) as conn:
        conn.execute("UPDATE accounts SET bio = ? WHERE user_id = ?", (bio, user_id))

def set_status(user_id, new_status, level=1):
    with sqlite3.connect(DB_ID) as conn:
        conn.execute("UPDATE accounts SET global_status = ?, level = ? WHERE user_id = ?", (new_status, level, user_id))

def get_user_level(user_id: int) -> str:
    data = get_id(user_id)
    level_num = data[10] if data else 1
    return LEVEL.get(level_num, f"{level_num} ур.")