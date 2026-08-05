import sqlite3
import random
import string
from datetime import datetime

DB_ID = "openbot_id.db"

STATUS = {
    "developer": "🌐💠 Openbot.Ai",
    "tester": "🌐 Тестер",
    "admin": "⭐ Администратор",
    "user": "👤 Игрок",
    "frozen": "❄️ Заморожен",
    "banned": "☠️ Забаненный",
}

def init_id_system():
    conn = sqlite3.connect(DB_ID) # Простое соединение
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
            is_public INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close() # Обязательно закрываем!

def is_globally_banned(user_id):
    """Проверяет, не забанен ли пользователь в глобальной системе."""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT global_status FROM accounts WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        # Если статус 'banned' или 'frozen', возвращаем True
        return row and row[0] in ("banned", "frozen")

def get_active_bots(user_id):
    """Возвращает список ботов, в которых играет пользователь."""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_bots FROM accounts WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row and row[0]:
            return row[0].split(',')
        return []

def register_bot_activity(user_id, bot_name):
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT active_bots FROM accounts WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        
        if row:
            current_bots = row[0].split(',') if row[0] else []
            if bot_name not in current_bots:
                current_bots.append(bot_name)
                new_bots_str = ",".join(current_bots)
                cursor.execute("UPDATE accounts SET active_bots = ? WHERE user_id = ?", (new_bots_str, user_id))

def get_id(user_id):
    """Получение полных данных аккаунта по user_id."""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, active_bots, name, emoji, old_emoji, player_tag, global_status, created_at, bio, is_public "
            "FROM accounts WHERE user_id = ?", 
            (user_id,)
        )
        return cursor.fetchone()

def get_id_by_tag(tag):
    """Поиск профиля по уникальному тегу."""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, name, emoji, player_tag, global_status, created_at FROM accounts WHERE player_tag = ?", 
            (tag,)
        )
        return cursor.fetchone()

def create_id(user_id, name, emoji=None):
    """Создание уникального OpenbotAI ID."""
    tag = "#" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    date = datetime.now().strftime("%d.%m.%Y")
    
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO accounts (user_id, name, emoji, player_tag, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, emoji, tag, date)
            )
        except sqlite3.IntegrityError:
            return None
    return tag

def update_name(user_id, new_name):
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET name = ? WHERE user_id = ?", (new_name, user_id))

def update_emoji(user_id, emoji):
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET emoji = ? WHERE user_id = ?", (emoji, user_id))

def update_bio(user_id, bio):
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE accounts SET bio = ? WHERE user_id = ?", (bio, user_id))

def set_status(user_id, new_status):
    """Смена глобального статуса с сохранением/восстановлением эмодзи при блокировках."""
    with sqlite3.connect(DB_ID) as conn:
        cursor = conn.cursor()
        
        if new_status in ("banned", "frozen"):
            cursor.execute("SELECT emoji FROM accounts WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            old_emoji = row[0] if row else None
            new_emoji = "❄️" if new_status == "frozen" else "🚫"
            
            cursor.execute(
                "UPDATE accounts SET global_status = ?, old_emoji = ?, emoji = ? WHERE user_id = ?",
                (new_status, old_emoji, new_emoji, user_id)
            )
        elif new_status == "user":
            cursor.execute("SELECT old_emoji FROM accounts WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            old_emoji = row[0] if row else None
            
            cursor.execute(
                "UPDATE accounts SET global_status = ?, emoji = ?, old_emoji = NULL WHERE user_id = ?",
                (new_status, old_emoji, user_id)
            )
        else:
            cursor.execute("UPDATE accounts SET global_status = ? WHERE user_id = ?", (new_status, user_id))

def check_id_ban(user_id):
    res = get_id(user_id)
    return True if res and res[6] == "banned" else False

def check_id_frozen(user_id):
    res = get_id(user_id)
    return True if res and res[6] == "frozen" else False