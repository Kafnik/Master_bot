import telebot
import os
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import openbot_api
import sqlite3
import random
import time
import traceback
import logging
import html
import re
import master_winter_db
from openbot_api import ROLE
from telebot import apihelper
from datetime import datetime
from telebot import types
from telebot.types import Message
from pathlib import Path
from telebot.handler_backends import BaseMiddleware, CancelUpdate
from logging.handlers import RotatingFileHandler


# ======= Настройка ======
invoice_messages = {}
DB_NAME = str(Path(__file__).resolve().parent / "Master_bot.1.7.db")
WINTER_DB_NAME = "Master_winter_2026.db"  # база события; если модуль используют два бота — дай каждому своё имя
DEVELOPER_CHAT_ID = 123456789
# ========================

# Настройки бота
TOKEN = 'token'
OPENBOT_API_KEY = 'token'


bot = telebot.TeleBot(TOKEN, use_class_middlewares=True)

openbot = openbot_api.OpenbotAPI(api_key=OPENBOT_API_KEY, bot=bot)

# ++++++++++++++++++++++++++++++++
MAINTENANCE_ALLOWED = {"developer", "owner", "admin", "tester"}
# Кто может ВКЛЮЧАТЬ/ВЫКЛЮЧАТЬ режим (уберите "admin", если нужно только разработчикам)
MAINTENANCE_SWITCHERS = {"developer", "owner", "admin"}
MAINTENANCE_TEXT = ("🛠 MASTER BOT · ТЕХНИЧЕСКИЕ РАБОТЫ\n"
                    "━━━━━━━━━━━━━━━━━━\n\n"
                    "❄️ Бот временно закрыт для игроков.\n"
                    "🔧 Команда Openbot.Ai проводит обслуживание.\n\n"
                    "Пожалуйста, попробуй позже.\n"
                    "🌐💠 Openbot.Ai")
 
@bot.message_handler(commands=['test_error'])
def test_error(message):
    # Команда доступна только разработчику
    if message.from_user.id != DEVELOPER_CHAT_ID:
        return

    bot.reply_to(message, "🧪 Проверяю обработчик ошибок...")

    # Специально вызываем ошибку
    raise RuntimeError("Тестовая ошибка! Проверка GlobalExceptionHandler.")


@bot.message_handler(commands=['logs'])
def show_error_logs(message):
    if message.chat.type != 'private' or message.from_user.id != DEVELOPER_CHAT_ID:
        return

    try:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
            bot.reply_to(message, "✅ Ошибок пока нет.")
            return

        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Последние 15 строк, максимум 1500 символов
        logs = "".join(lines[-15:])[-1500:]

        safe_logs = html.escape(logs)

        bot.reply_to(
            message,
            "📋 <b>Последние ошибки:</b>\n\n"
            f'<pre><code class="language-python">{safe_logs}</code></pre>',
            parse_mode="HTML"
        )

    except Exception:
        error_logger.exception("Ошибка чтения логов")
        bot.reply_to(message, "❌ Не удалось прочитать логи.")

def is_maintenance() -> bool:
    """Читает флаг из БД: 1 — тех. обслуживание включено, 0 — выключено."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM system_settings WHERE key = 'maintenance'")
        row = cur.fetchone()
        return bool(row) and str(row[0]).strip() == "1"
    except Exception as e:
        print(f"[Maintenance] Не удалось прочитать флаг: {e}")
        return False  # при сбое БД не блокируем всех
 
 
def set_maintenance(enabled: bool) -> None:
    """Меняет в БД maintenance: 0 -> 1 (включить) или 1 -> 0 (выключить)."""
    conn.execute(
        "INSERT INTO system_settings (key, value) VALUES ('maintenance', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if enabled else "0",),
    )
    conn.commit()
 
 
def maintenance_allowed(user_id: int) -> bool:
    """Служебные роли обходят обычные техработы, но не наказания."""
    return _master_staff(user_id)
 
 
class MaintenanceMiddleware(BaseMiddleware):
    """Один общий «вахтёр»: до любого обработчика пропускает только допущенных, пока режим включён."""
 
    def __init__(self):
        super().__init__()
        self.update_types = ["message", "callback_query"]
 
    def pre_process(self, message, data):
        if not is_maintenance():
            return None
        user = getattr(message, "from_user", None)
        if user is None or maintenance_allowed(user.id):
            return None  # свои — проходят
        try:
            if hasattr(message, "data"):  # нажатие inline-кнопки
                bot.answer_callback_query(message.id, '🛠 Бот временно закрыт', show_alert=True)
                bot.send_message(message.message.chat.id, _tech_full_notice(), parse_mode='HTML')
            else:  # обычное сообщение
                bot.send_message(message.chat.id, _tech_full_notice(), parse_mode='HTML')
        except Exception as e:
            print(f"[Maintenance] Не удалось ответить: {e}")
        return CancelUpdate()  # дальше обработчики НЕ вызываются
 
    def post_process(self, message, data, exception):
        pass
 
bot.setup_middleware(MaintenanceMiddleware())


def get_openbot_user(user_id):
    """Глобальные данные игрока из openbot (локально, без HTTP): {'status', 'level'} или None."""
    try:
        data = openbot.get_id(user_id)
        if not data:
            return None
        return {
            "status": str(data[6] or "user").strip().lower(),
            "level": int(data[10] or 1),
        }
    except Exception as e:
        print(f"[OpenBot ID] Ошибка чтения данных {user_id}: {e}")
        return None


# -================================
# ======= Переменные ======
ALLOWED_ROLES = ["developer", "tester", "admin"]
STATUS = {
    "developer": "🌐💠 Openbot.Ai",
    "tester": "🌐 Тестер",
    "coder": "🌐 Кодер",
    "admin": "⭐ Администратор",
    "user": "👤 Игрок",
    "banned": "🚫 Забаненный",
}

PROFILE_TITLES = {
    "none": "Без титула", "veteran": "🎖 Ветеран", "king": "👑 Король", "legend_50": "🌟 Легенда",
    "pro": "🔥 Профи", "legend": "👑 Легенда",
}

# ========== База ========
conn = sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    cursor = conn.cursor()
    
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # По умолчанию тех. режим ВЫКЛЮЧЕН ('0')
    cursor.execute("""
            INSERT OR IGNORE INTO system_settings (key, value) 
            VALUES ('maintenance', '0')
        """)
    conn.commit()
     
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            first_name TEXT,
            status TEXT DEFAULT 'user',
            profile_title TEXT DEFAULT  NULL,
            coins INTEGER DEFAULT 0,
            key_coleso DEFAULT 0,
            level INTEGER DEFAULT 1,
            XP INTEGER DEFAULT 100
       )
    """)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role_level INTEGER DEFAULT 1")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT NULL")
    except:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_games (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER,
            game_type TEXT,
            secret_number INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 5,
            secret_code TEXT DEFAULT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_type TEXT,
            item_value TEXT,
            is_active INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS case_inventory (
            user_id INTEGER NOT NULL,
            case_key TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, case_key)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_property (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            property_name TEXT,
            property_type TEXT,
            income INTEGER       
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auction (
            lot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER,
            item_type TEXT,
            item_value TEXT,
            price INTEGER
        )
    """)

    try:
       cursor = conn.cursor()
       cursor.execute("ALTER TABLE users ADD COLUMN equipped_item TEXT DEFAULT ''")
       conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN kb_type TEXT DEFAULT 'inline'")
        cursor.execute("ALTER TABLE users ADD COLUMN notifications INTEGER DEFAULT 1")
        cursor.execute("ALTER TABLE users ADD COLUMN show_id_to_others INTEGER DEFAULT 1")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN badge TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    try:
       cursor.execute("ALTER TABLE users ADD COLUMN badge TEXT DEFAULT NULL")
       conn.commit()
    except sqlite3.OperationalError:
       pass
    try:
       cursor.execute("ALTER TABLE users ADD COLUMN snowflakes INTEGER DEFAULT 0")
       conn.commit()
    except sqlite3.OperationalError:
       pass
    conn.commit()

init_db()

# Сложность новых крестиков-ноликов хранится в active_games.state_json.
if "state_json" not in {row[1] for row in conn.execute("PRAGMA table_info(active_games)")}:
    conn.execute("ALTER TABLE active_games ADD COLUMN state_json TEXT DEFAULT '{}'")
    conn.commit()

# Idempotent schema checks for existing Master Bot databases.
def _ensure_master_columns():
    additions = {'kb_type': "TEXT DEFAULT 'inline'", 'notifications': 'INTEGER DEFAULT 1',
                 'show_id_to_others': 'INTEGER DEFAULT 1', 'badge': "TEXT DEFAULT ''",
                 'equipped_item': "TEXT DEFAULT ''", 'ban_reason': 'TEXT',
                 'role_level': 'INTEGER DEFAULT 1', 'global_ban_kind': 'TEXT',
                 'snowballs': 'INTEGER DEFAULT 0'}
    existing = {row[1] for row in conn.execute('PRAGMA table_info(users)')}
    for column, definition in additions.items():
        if column not in existing:
            conn.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')
    conn.commit()

_ensure_master_columns()

# ======= Независимая зимняя база события (снежинки, кейсы, титулы) =======
# Старые таблицы/столбцы события в основной базе (users.snowflakes, case_inventory) больше НЕ используются:
# данные теперь живут в Master_winter_2026.db. В основной базе они оставлены как резервная копия.
master_winter_db.init(WINTER_DB_NAME)

# ======= Вспомогательные функции =======
def get_user_settings(user_id):
    with sqlite3.connect(DB_NAME, timeout=10) as db:
        res = db.execute(
            'SELECT kb_type, notifications, show_id_to_others FROM users WHERE user_id=?',
            (user_id,)
        ).fetchone()
    kb_type = (res[0] or 'inline') if res else 'inline'
    if _reply_off_enabled() and _reply_regular_user(user_id):
        kb_type = 'inline'
    notifications = (res[1] if res[1] is not None else 1) if res else 1
    show_id = (res[2] if res[2] is not None else 1) if res else 1
    return kb_type, notifications, show_id


def toggle_user_setting(user_id, setting_name):
    kb_type, notifications, show_id = get_user_settings(user_id)

    cursor = conn.cursor()

    if setting_name == 'kb_type':
        if kb_type == 'inline' and _reply_off_enabled() and _reply_regular_user(user_id):
            return 'inline'
        if kb_type == 'inline':
            new_val = 'reply'
        else:
            new_val = 'inline'

        cursor.execute(
            "UPDATE users SET kb_type = ? WHERE user_id = ?",
            (new_val, user_id)
        )

        conn.commit()
        return new_val

    elif setting_name == 'notifications':
        new_val = 0 if notifications == 1 else 1

        cursor.execute(
            "UPDATE users SET notifications = ? WHERE user_id = ?",
            (new_val, user_id)
        )

        conn.commit()
        return new_val

    elif setting_name == 'show_id':
        new_val = 0 if show_id == 1 else 1

        cursor.execute(
            "UPDATE users SET show_id_to_others = ? WHERE user_id = ?",
            (new_val, user_id)
        )

        conn.commit()
        return new_val

    
def get_progress_bar(current, total=100, length=8):
    """Генератор визуального прогресс-бара"""
    percent = min(max(current / total, 0), 1) if total > 0 else 0
    filled_length = int(length * percent)
    bar = "🟩" * filled_length + "⬜" * (length - filled_length)
    return f"[{bar}] {int(percent * 100)}%"

def sync_global_status(user_id, *, global_ban=None):
    """Синхронизирует отображаемый статус без изменения роли в Openbot ID.

    Исходная роль берётся из центрального Openbot ID. Локальные и глобальные
    наказания не изменяют её, а только временно показывают «Забаненный».
    Если все наказания сняты, сразу возвращается исходная роль (developer и т.д.).
    """
    try:
        global_user = get_openbot_user(user_id)
        if not global_user:
            return False

        if global_ban is None:
            global_ban = openbot.get_global_ban(user_id)
        if not isinstance(global_ban, dict) or not isinstance(
            global_ban.get('banned'), bool
        ):
            raise ValueError('Openbot API не вернул статус глобального бана')

        local_ban = _punishment_get(user_id)
        global_kind = (
            global_ban.get('kind') if global_ban['banned'] else None
        )
        role = str(global_user.get('status') or 'user').lower()
        level = int(global_user.get('level') or 1)
        status = 'banned' if local_ban or global_kind else role
        reason = (
            local_ban.get('reason') if local_ban
            else global_ban.get('reason') if global_kind else None
        )

        with conn:
            row = conn.execute(
                'SELECT status, role_level, ban_reason, global_ban_kind '
                'FROM users WHERE user_id=?', (user_id,),
            ).fetchone()
            if not row:
                return False
            values = (status, level, reason, global_kind)
            if tuple(row) == values:
                return False
            conn.execute(
                'UPDATE users SET status=?, role_level=?, '
                'ban_reason=?, global_ban_kind=? WHERE user_id=?',
                (*values, user_id),
            )
        return True
    except Exception as exc:
        logging.getLogger('master_bot.status').warning(
            'Не удалось синхронизировать статус user_id=%s: %s',
            user_id, exc,
        )
        return False



def get_local_role(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT status, role_level FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return 'user', 1
    return str(row[0] or 'user').lower(), int(row[1] or 1)

def get_user(user_id):
    # При любом обращении к данным пользователя сначала обновляем
    # локальный статус/уровень роли из OpenBot ID.
    sync_global_status(user_id)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def get_equipped_emoji(user_id):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT equipped_item FROM users WHERE user_id = ?",
        (user_id,)
    )
    row = cursor.fetchone()
    equipped = row[0] if row and row[0] else ""
    if not equipped:
        return ""

    # Если надетого эмодзи уже нет в инвентаре (продали, выставили на рынок, подарили) — снимаем его
    cursor.execute(
        "SELECT 1 FROM inventory WHERE user_id = ? AND item_value = ? LIMIT 1",
        (user_id, equipped)
    )
    if cursor.fetchone():
        return equipped
    cursor.execute("UPDATE users SET equipped_item = '' WHERE user_id = ?", (user_id,))
    conn.commit()
    return ""

def unequip_if_no_copies(cursor, user_id, emoji):
    """Снимает эмодзи, если оно надето, а копий в инвентаре не осталось.
    Вызывать после удаления эмодзи из инвентаря (до conn.commit()). True — эмодзи было снято."""
    cursor.execute(
        "SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_type = 'emoji' AND item_value = ?",
        (user_id, emoji)
    )
    if cursor.fetchone()[0] > 0:
        return False
    cursor.execute(
        "UPDATE users SET equipped_item = '' WHERE user_id = ? AND equipped_item = ?",
        (user_id, emoji)
    )
    return cursor.rowcount == 1

def create_user(user_id, username, first_name):
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, first_name, status, role_level) VALUES (?, ?, ?, 'user', 1)",
            (user_id, username.lower() if username else None, first_name))
        conn.commit()

def add_coins(user_id, amount):
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def set_active_game(user_id, chat_id, game_type, secret_number=0, attempts=5, secret_code=None):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO active_games (user_id, chat_id, game_type, secret_number, attempts, secret_code) 
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, chat_id, game_type, secret_number, attempts, secret_code))
    conn.commit()

def get_active_game(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT game_type, secret_number, attempts, secret_code FROM active_games WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def update_game_attempts(user_id, attempts):
    cursor = conn.cursor()
    cursor.execute("UPDATE active_games SET attempts = ? WHERE user_id = ?", (attempts, user_id))
    conn.commit()

def delete_active_game(user_id):
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_games WHERE user_id = ?", (user_id,))
    conn.commit()

def buy_property(user_id, property_name, property_type, price, income):
    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    coins = cursor.fetchone()[0]
    if coins < price:
        return False
    cursor.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (price, user_id))
    cursor.execute("INSERT INTO user_property (user_id, property_name, property_type, income) VALUES (?, ?, ?, ?)",
                   (user_id, property_name, property_type, income))
    conn.commit()
    return True

def get_user_properties(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT property_name, income FROM user_property WHERE user_id = ?", (user_id,))
    return cursor.fetchall()

def count_matched_digits(secret, guess):
    return sum(1 for s, g in zip(secret, guess) if s == g)

def has_access(user_id):
    """Доступ для команды (developer / tester / admin / owner) — через openbot."""
    return openbot.check_access(user_id, ("developer", "tester", "admin", "owner"))

def check_ban(user_id):
    """Проверка только старых локальных банов без записи в punishments.

    Глобальные баны и новые локальные блокировки контролируют middleware.
    Нельзя блокировать профиль при обычном GLOBAL BAN только из-за того, что
    статус локально отображается как «Забаненный».
    """
    row = conn.execute(
        'SELECT status, ban_reason, global_ban_kind '
        'FROM users WHERE user_id=?', (user_id,),
    ).fetchone()
    if not row or row[0] != 'banned' or row[2]:
        return None
    if _punishment_get(user_id) is not None:
        return None
    return row[1] or None


# ========= спомогательные функции ==========
def add_snowballs(user_id, amount):
    """Snowballs from games; separate from ❄️ winter snowflakes."""
    amount = int(amount)
    if amount <= 0:
        return 0
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        result = db.execute('UPDATE users SET snowballs=COALESCE(snowballs,0)+? WHERE user_id=?',
                            (amount, user_id))
    return amount if result.rowcount == 1 else 0


def get_snowballs(user_id):
    row = conn.execute('SELECT COALESCE(snowballs,0) FROM users WHERE user_id=?',
                       (user_id,)).fetchone()
    return int(row[0]) if row else 0


def add_xp(user_id, amount):
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET XP = XP + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

def add_to_inventory(user_id, item_value, item_type='emoji'):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO inventory (user_id, item_type, item_value) VALUES (?, ?, ?)", (user_id, item_type, item_value))
    conn.commit()

def update_game_state(user_id, secret_number=0, attempts=5, secret_code=None):
    cursor = conn.cursor()
    cursor.execute("UPDATE active_games SET secret_number = ?, attempts = ?, secret_code = ? WHERE user_id = ?", 
                   (secret_number, attempts, secret_code, user_id))
    conn.commit()

# ===== Крестики-нолики: три уровня ИИ (0=пусто, 1=игрок, 2=бот) =====
from functools import lru_cache

_TTT_LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
              (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
_TTT_LEVELS = {'easy': '🟢 Лёгкий', 'normal': '🟡 Средний', 'hard': '🔴 Сложный'}
_TTT_REWARDS = {'easy': (20, 10, 1), 'normal': (40, 25, 2), 'hard': (80, 50, 3)}


def ttt_winner(board):
    for a, b, c in _TTT_LINES:
        if board[a] != '0' and board[a] == board[b] == board[c]:
            return board[a]
    return 'draw' if '0' not in board else None


@lru_cache(maxsize=10000)
def _ttt_minimax(board_str, player):
    result = ttt_winner(board_str)
    if result == '2':
        return 1
    if result == '1':
        return -1
    if result == 'draw':
        return 0
    possible = []
    for i, char in enumerate(board_str):
        if char == '0':
            next_board = board_str[:i] + player + board_str[i + 1:]
            possible.append(_ttt_minimax(next_board, '1' if player == '2' else '2'))
    return max(possible) if player == '2' else min(possible)


def ttt_ai_move(board, difficulty='hard'):
    """Три сложности. Сложный ИИ иногда рискует, но не пропускает мгновенную угрозу."""
    options = [i for i, v in enumerate(board) if v == '0']
    if not options or ttt_winner(board):
        return None

    # Простой уровень почти всегда выбирает случайную клетку.
    if difficulty == 'easy':
        return random.choice(options)

    def after_move(pos, symbol):
        candidate = board.copy()
        candidate[pos] = symbol
        return candidate

    # Выигрыш за один ход приоритетнее любого случайного решения.
    wins = [p for p in options if ttt_winner(after_move(p, '2')) == '2']
    if wins:
        return random.choice(wins)

    # На сложном уровне защита от немедленного проигрыша обязательна.
    blocks = [p for p in options if ttt_winner(after_move(p, '1')) == '1']
    if blocks and difficulty == 'hard':
        return random.choice(blocks)

    # Промежуточный уровень иногда не замечает опасный ход.
    if difficulty == 'normal' and random.random() < 0.35:
        return random.choice(options)

    board_str = ''.join(board)
    priority = (4, 0, 2, 6, 8, 1, 3, 5, 7)
    moves = []
    for pos in options:
        candidate = board_str[:pos] + '2' + board_str[pos + 1:]
        value = _ttt_minimax(candidate, '1')
        # При равном результате используем не только центр, но и разные ходы.
        moves.append((pos, value))

    best_score = max(score for _, score in moves)
    best_moves = [pos for pos, score in moves if score == best_score]
    alternatives = [pos for pos, score in moves if score < best_score]

    # 18%: сложный ИИ выбирает рискованный ход только при отсутствии
    # прямой угрозы и возможности немедленно победить. Поэтому ничьих меньше.
    if difficulty == 'hard' and alternatives and random.random() < 0.18:
        # Отсеиваем совсем грубые ошибки: не даём игроку выиграть одним ходом.
        tolerable = []
        for pos in alternatives:
            next_board = after_move(pos, '2')
            available = [i for i, v in enumerate(next_board) if v == '0']
            if not any(ttt_winner(next_board[:i] + ['1'] + next_board[i+1:]) == '1'
                       for i in available):
                tolerable.append(pos)
        if tolerable:
            return random.choice(tolerable)

    # По возможности делаем игру разнообразной среди одинаково сильных ходов.
    if difficulty == 'hard':
        return random.choice(best_moves)
    return min(best_moves, key=lambda pos: priority.index(pos))


def ttt_get_level(user_id):
    row = conn.execute('SELECT state_json FROM active_games WHERE user_id=? AND game_type=?',
                       (user_id, 'ttt')).fetchone()
    if row and row[0]:
        try:
            level = json.loads(row[0]).get('difficulty', 'normal')
            if level in _TTT_LEVELS:
                return level
        except (ValueError, TypeError, AttributeError):
            pass
    return 'normal'


def ttt_start(user_id, chat_id, level):
    if level not in _TTT_LEVELS:
        raise ValueError('Неверная сложность')
    set_active_game(user_id, chat_id, 'ttt', secret_code='0' * 9)
    conn.execute('UPDATE active_games SET state_json=? WHERE user_id=?',
                 (json.dumps({'difficulty': level}), user_id))
    conn.commit()


def ttt_level_markup(reply=False):
    if reply:
        kb = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        for key, title in _TTT_LEVELS.items():
            kb.add(types.KeyboardButton(f'❌⭕ {title}'))
        kb.add(types.KeyboardButton('⬅ Игры'))
        return kb
    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, title in _TTT_LEVELS.items():
        kb.add(types.InlineKeyboardButton(title, callback_data=f'ttt_level_{key}'))
    kb.add(types.InlineKeyboardButton('⬅ Игры', callback_data='game'))
    return kb


def ttt_play(user_id, pos):
    """Применить ход и выдать финальную награду; возвращает (результат, доска, текст)."""
    game = get_active_game(user_id)
    if not game or game[0] != 'ttt':
        return 'invalid', None, 'Игра не активна. Выбери сложность заново.'
    board_text = game[3]
    if not isinstance(board_text, str) or len(board_text) != 9 or any(x not in '012' for x in board_text):
        return 'invalid', None, 'Некорректная доска. Начни игру заново.'
    board = list(board_text)
    if not isinstance(pos, int) or pos not in range(9) or board[pos] != '0' or ttt_winner(board):
        return 'invalid', board_text, 'Эта клетка занята или игра уже завершена.'
    level = ttt_get_level(user_id)
    board[pos] = '1'
    result = ttt_winner(board)
    if result is None:
        ai = ttt_ai_move(board, level)
        if ai is not None:
            board[ai] = '2'
        result = ttt_winner(board)
    board_str = ''.join(board)
    if result:
        delete_active_game(user_id)
        if result == '1':
            coins, xp, snowballs = _TTT_REWARDS[level]
            add_coins(user_id, coins)
            add_xp(user_id, xp)
            add_snowballs(user_id, snowballs)
            return 'done', board_str, (f'🎉 <b>Победа!</b>\n💰 +{coins} монет · ⭐ +{xp} XP'
                                       f' · ☃️ +{snowballs} снежков')
        if result == '2':
            return 'done', board_str, '🤖 <b>ИИ победил!</b> Попробуй ещё раз.'
        add_snowballs(user_id, 1)
        return 'done', board_str, '🤝 <b>Ничья!</b> Хорошая партия.\n☃️ +1 снежок'
    update_game_state(user_id, secret_code=board_str)
    return 'playing', board_str, f'❌⭕ <b>Крестики-нолики</b>\nСложность: {_TTT_LEVELS[level]}\n\nТвой ход: ❌'


def ttt_board_text(board):
    # Пробел вместо значка в незанятой клетке.
    icons = {'0': '　', '1': '❌', '2': '⭕'}
    return '\n'.join(' '.join(icons[ch] for ch in board[i:i+3]) for i in (0, 3, 6))


def build_ttt_keyboard(board_str, finished=False):
    """Поле 3×3: незанятые кнопки без видимого символа.

    Telegram сам управляет фоном кнопок, сделать его прозрачным через Bot API нельзя.
    Невидимый U+200B оставляет место для нажатия, не рисуя квадрат/эмодзи.
    """
    markup = types.InlineKeyboardMarkup(row_width=3)
    symbols = {'0': '\u200b', '1': '❌', '2': '⭕'}
    for start in range(0, 9, 3):
        row = []
        for i in range(start, start + 3):
            row.append(types.InlineKeyboardButton(
                symbols[board_str[i]],
                callback_data='ttt_done' if finished else f'ttt_pos_{i}'
            ))
        markup.row(*row)
    if not finished:
        markup.add(types.InlineKeyboardButton('🛑 Выход из игры', callback_data='back_to_games'))
    return markup

# ============= снежинки =============
SNOWFLAKE_DROP_CHANCE = 0.03  # 3% шанс получить снежинку

WINTER_CHEST_REWARDS = [
    ("⛄", "Обычная", 45.0),
    ("🧣", "Редкая", 30.0),
    ("❄️", "Очень редкая", 15.0),
    ("🎁", "Эпическая", 7.0),
    ("🌟", "Мифическая", 2.5),
    ("🎆", "Легендарная", 0.5),
]

def try_drop_snowflake(user_id, callback_id=None):
    if random.random() <= SNOWFLAKE_DROP_CHANCE:
        master_winter_db.add_keys(user_id, 1, "drop")

        if callback_id:
            try:
                bot.answer_callback_query(
                    callback_id,
                    "❄️ Тебе выпал снежинка!",
                    show_alert=True
                )
            except Exception:
                pass

        return True

    return False

def get_snowflakes(user_id):
    return master_winter_db.get_keys(user_id)


def open_winter_chest(user_id):
    """Тратит 1 ключ (база события) и выдаёт случайный эмодзи или монеты за дубликат (основная база)."""

    # Ключ списывается в базе события; если ключей нет — сундук не открывается
    if not master_winter_db.spend_keys(user_id, 1, "chest"):
        return None

    emoji, rarity, _ = random.choices(
        WINTER_CHEST_REWARDS,
        weights=[x[2] for x in WINTER_CHEST_REWARDS],
        k=1
    )[0]

    try:
        cursor = conn.cursor()

        # Проверяем, есть ли уже такой эмодзи
        cursor.execute(
            """
            SELECT id
            FROM inventory
            WHERE user_id = ?
            AND item_type = 'emoji'
            AND item_value = ?
            LIMIT 1
            """,
            (user_id, emoji)
        )

        duplicate = cursor.fetchone()

        if duplicate:

            compensation = {
                "Обычная": 25,
                "Редкая": 50,
                "Очень редкая": 100,
                "Эпическая": 200,
                "Мифическая": 500,
                "Легендарная": 1000
            }[rarity]

            cursor.execute(
                "UPDATE users SET coins = coins + ? WHERE user_id = ?",
                (compensation, user_id)
            )

            result = {
                "emoji": emoji,
                "rarity": rarity,
                "coins": compensation
            }

        else:

            cursor.execute(
                """
                INSERT INTO inventory
                (user_id, item_type, item_value)
                VALUES (?, 'emoji', ?)
                """,
                (user_id, emoji)
            )

            result = {
                "emoji": emoji,
                "rarity": rarity,
                "coins": 0
            }

        conn.commit()

    except Exception as e:
        # Награду выдать не удалось — возвращаем ключ, чтобы он не пропал
        conn.rollback()
        master_winter_db.add_keys(user_id, 1, "chest_refund")
        print(f"[Winter] Ошибка выдачи награды сундука: {e}")
        return None

    return result

# ============= КЕЙСЫ =============
# Количество кейсов хранится отдельно в case_inventory.
# Награды используют уже существующие в боте: монеты, XP, эмодзи и снежинки.
CASES = {

    "wood": {
        "name": "📦 Деревянный кейс",
        "description": "Первый кейс. Небольшие награды и обычные эмодзи.",
        "price": 500,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 300, "chance": 35},
            {"type": "coins", "amount": 750, "chance": 25},
            {"type": "xp", "amount": 100, "chance": 20},
            {"type": "emoji", "value": "🙂", "chance": 10},
            {"type": "emoji", "value": "😎", "chance": 7},
            {"type": "emoji", "value": "🤩", "chance": 3},
        ],
    },

    "silver": {
        "name": "🥈 Серебряный кейс",
        "description": "Больше монет, опыта и необычные эмодзи.",
        "price": 2_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 1_000, "chance": 30},
            {"type": "coins", "amount": 3_000, "chance": 25},
            {"type": "xp", "amount": 300, "chance": 20},
            {"type": "emoji", "value": "😱", "chance": 10},
            {"type": "emoji", "value": "🤖", "chance": 8},
            {"type": "emoji", "value": "🦊", "chance": 5},
            {"type": "snowflake", "amount": 1, "chance": 2},
        ],
    },

    "gold": {
        "name": "🥇 Золотой кейс",
        "description": "Ценные награды и редкие эмодзи.",
        "price": 7_500,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 3_000, "chance": 30},
            {"type": "coins", "amount": 10_000, "chance": 25},
            {"type": "xp", "amount": 750, "chance": 18},
            {"type": "emoji", "value": "👑", "chance": 10},
            {"type": "emoji", "value": "🔥", "chance": 8},
            {"type": "emoji", "value": "❄️", "chance": 6},
            {"type": "snowflake", "amount": 1, "chance": 3},
        ],
    },

    "diamond": {
        "name": "💎 Алмазный кейс",
        "description": "Высокая стоимость, крупные награды и очень редкие эмодзи.",
        "price": 25_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 10_000, "chance": 28},
            {"type": "coins", "amount": 40_000, "chance": 24},
            {"type": "xp", "amount": 2_000, "chance": 18},
            {"type": "emoji", "value": "💎", "chance": 10},
            {"type": "emoji", "value": "⚡", "chance": 8},
            {"type": "emoji", "value": "🐉", "chance": 7},
            {"type": "snowflake", "amount": 1, "chance": 5},
        ],
    },

    "fire": {
        "name": "🔥 Огненный кейс",
        "description": "Большие суммы и легендарные эмодзи.",
        "price": 75_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 30_000, "chance": 30},
            {"type": "coins", "amount": 120_000, "chance": 22},
            {"type": "xp", "amount": 5_000, "chance": 18},
            {"type": "emoji", "value": "🔥", "chance": 10},
            {"type": "emoji", "value": "😈", "chance": 8},
            {"type": "emoji", "value": "👹", "chance": 7},
            {"type": "snowflake", "amount": 1, "chance": 5},
        ],
    },

    "cosmic": {
        "name": "🌌 Космический кейс",
        "description": "Очень дорогой кейс с эпическими и легендарными наградами.",
        "price": 250_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 100_000, "chance": 28},
            {"type": "coins", "amount": 400_000, "chance": 22},
            {"type": "xp", "amount": 10_000, "chance": 17},
            {"type": "emoji", "value": "🌌", "chance": 10},
            {"type": "emoji", "value": "🚀", "chance": 9},
            {"type": "emoji", "value": "🌠", "chance": 8},
            {"type": "snowflake", "amount": 1, "chance": 6},
        ],
    },

    "royal": {
        "name": "👑 Королевский кейс",
        "description": "Почти максимальный уровень. Эпические и легендарные эмодзи.",
        "price": 1_000_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 500_000, "chance": 30},
            {"type": "coins", "amount": 2_000_000, "chance": 20},
            {"type": "xp", "amount": 25_000, "chance": 15},
            {"type": "emoji", "value": "👑", "chance": 12},
            {"type": "emoji", "value": "💰", "chance": 10},
            {"type": "emoji", "value": "🦄", "chance": 8},
            {"type": "snowflake", "amount": 1, "chance": 5},
        ],
    },

    "void": {
        "name": "🕳️ Кейс Пустоты",
        "description": "Абсолютно эндгейм-кейс. Только мифические эмодзи и огромные награды.",
        "price": 5_000_000,
        "currency": "coins",
        "rewards": [
            {"type": "coins", "amount": 2_000_000, "chance": 30},
            {"type": "coins", "amount": 10_000_000, "chance": 18},
            {"type": "xp", "amount": 100_000, "chance": 15},

            # 🔴 МИФИЧЕСКИЕ ЭМОДЗИ
            {"type": "emoji", "value": "❤️", "chance": 7},
            {"type": "emoji", "value": "💜", "chance": 6},
            {"type": "emoji", "value": "💖", "chance": 5},
            {"type": "emoji", "value": "💕", "chance": 4},

            {"type": "snowflake", "amount": 1, "chance": 15},
        ],
    },
}


def _case_currency_info(case_info):
    if case_info["currency"] == "snowflakes":
        return "❄️", "snowflakes"
    return "💰", "coins"


def get_case_count(user_id, case_key):
    return master_winter_db.get_case_count(user_id, case_key)


CASES_PER_PAGE = 3


def get_cases_page(page=0):
    case_items = list(CASES.items())
    total_pages = max(1, (len(case_items) + CASES_PER_PAGE - 1) // CASES_PER_PAGE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * CASES_PER_PAGE
    return case_items[start:start + CASES_PER_PAGE], page, total_pages


def build_cases_keyboard(user_id, page=0):
    case_items, page, total_pages = get_cases_page(page)
    kb = types.InlineKeyboardMarkup(row_width=2)

    for case_key, c_info in case_items:
        count = get_case_count(user_id, case_key)
        currency_icon, _ = _case_currency_info(c_info)
        kb.add(
            types.InlineKeyboardButton(
                f"🛒 Купить ({c_info['price']:,} {currency_icon})".replace(",", " "),
                callback_data=f"buy_case_{case_key}_{page}",
            ),
            types.InlineKeyboardButton(
                f"🔓 Открыть ({count} шт)",
                callback_data=f"open_case_{case_key}_{page}",
            ),
        )

    # Листание кейсов
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️", callback_data=f"casepage_{page - 1}"))
    nav.append(types.InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="ignore_click"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("➡️", callback_data=f"casepage_{page + 1}"))
    kb.row(*nav)

    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return kb


def balance_line(user_id):
    """Строка с балансом: монеты и ключи."""
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(coins, 0) FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    coins = int(row[0]) if row else 0
    return (f'💼 Баланс: <b>{_fmt(coins)}</b> 💰 · <b>{_fmt(get_snowflakes(user_id))}</b> ❄️'
            f' · <b>{_fmt(get_snowballs(user_id))}</b> ☃️')


def cases_text(user_id, page=0):
    case_items, page, total_pages = get_cases_page(page)
    lines = [
        "🎁 <b>КЕЙСЫ</b>",
        balance_line(user_id),
        "",
        "Открывай кейсы: 💰 монеты, ⭐ XP, ❄️ снежинки, 🎒 эмодзи и редкие 🏆 титулы.",
        f"📄 Страница <b>{page + 1}/{total_pages}</b>",
        "",
    ]
    for case_key, c_info in case_items:
        count = get_case_count(user_id, case_key)
        icon, _ = _case_currency_info(c_info)
        lines.append(
            f"{c_info['name']} — <b>{c_info['price']:,} {icon}</b> · у тебя: <b>{count}</b>".replace(",", " ")
        )
    return "\n".join(lines)


def buy_case(user_id, case_key):
    c_info = CASES.get(case_key)
    if not c_info:
        return False, "❌ Кейс не найден!"

    currency = c_info["currency"]
    price = int(c_info["price"])
    icon = "❄️" if currency == "snowflakes" else "💰"
    not_enough = f"❌ Недостаточно валюты! Нужно {price:,} {icon}.".replace(",", " ")

    if currency == "snowflakes":
        # Ключи лежат в базе события
        if not master_winter_db.spend_keys(user_id, price, f"buy_case:{case_key}"):
            return False, not_enough
        try:
            master_winter_db.add_case(user_id, case_key, 1, "buy")
        except Exception as e:
            master_winter_db.add_keys(user_id, price, "buy_case_refund")
            print(f"[Winter] Ошибка покупки кейса: {e}")
            return False, "❌ Не удалось купить кейс, ключи возвращены."
    else:
        # Монеты лежат в основной базе
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET coins = coins - ? WHERE user_id = ? AND coins >= ?",
            (price, user_id, price),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False, not_enough
        conn.commit()
        try:
            master_winter_db.add_case(user_id, case_key, 1, "buy")
        except Exception as e:
            add_coins(user_id, price)
            print(f"[Winter] Ошибка покупки кейса: {e}")
            return False, "❌ Не удалось купить кейс, монеты возвращены."

    return True, f"🛒 Куплен {c_info['name']} за {price:,} {icon}!".replace(",", " ")


def open_case(user_id, case_key):
    c_info = CASES.get(case_key)
    if not c_info:
        return None, "❌ Кейс не найден!"
    if master_winter_db.get_case_count(user_id, case_key) <= 0:
        return None, "❌ У тебя нет такого кейса!"

    reward = random.choices(
        c_info["rewards"],
        weights=[r["chance"] for r in c_info["rewards"]],
        k=1,
    )[0]

    # Кейс забирается из базы события
    if not master_winter_db.take_case(user_id, case_key, "open"):
        return None, "❌ Не удалось открыть кейс."

    rtype = reward["type"]
    key_amount = 0
    try:
        cursor = conn.cursor()
        if rtype == "coins":
            amount = int(reward["amount"])
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
            text = f"💰 +{amount:,} монет".replace(",", " ")
        elif rtype == "xp":
            amount = int(reward["amount"])
            cursor.execute("UPDATE users SET XP = XP + ? WHERE user_id = ?", (amount, user_id))
            text = f"⭐ +{amount:,} XP".replace(",", " ")
        elif rtype == "snowflake":
            # Ключи выдаются в базе события — ниже, после основной базы
            key_amount = int(reward.get("amount", 1))
            text = f"❄️ +{key_amount} снежинка"
        elif rtype == "title":
            title_key = reward['value']
            title_name = PROFILE_TITLES.get(title_key, 'Зимний титул')
            row = cursor.execute('SELECT profile_title FROM users WHERE user_id=?', (user_id,)).fetchone()
            owned = set(master_winter_db.get_titles(user_id))
            if title_key in owned or (row and row[0] == title_key):
                compensation = max(250, int(CASES[case_key]['price']) // 5)
                cursor.execute('UPDATE users SET coins=coins+? WHERE user_id=?',
                               (compensation, user_id))
                text = f'🏆 Дубликат «{title_name}» → 💰 +{_fmt(compensation)} монет'
            else:
                if not master_winter_db.record_title(user_id, title_key, 0):
                    compensation = max(250, int(CASES[case_key]['price']) // 5)
                    cursor.execute('UPDATE users SET coins=coins+? WHERE user_id=?',
                                   (compensation, user_id))
                    text = f'🏆 Дубликат «{title_name}» → 💰 +{_fmt(compensation)} монет'
                else:
                    text = f'🏆 Новый титул: <b>{html.escape(title_name)}</b>! Выбери его в «Мои титулы».'
        elif rtype == "emoji":
            emoji = reward["value"]
            cursor.execute(
                "SELECT id FROM inventory WHERE user_id = ? AND item_type = 'emoji' AND item_value = ? LIMIT 1",
                (user_id, emoji),
            )
            duplicate = cursor.fetchone()
            if duplicate:
                compensation = {"Обычная": 50, "Необычная": 100, "Редкая": 250, "Очень редкая": 500, "Эпическая": 1000, "Мифическая": 2500, "Легендарная": 5000}.get(get_emoji_rarity(emoji), 100)
                cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (compensation, user_id))
                text = f"{emoji} Дубликат → 💰 +{compensation} монет"
            else:
                cursor.execute(
                    "INSERT INTO inventory (user_id, item_type, item_value, is_active) VALUES (?, 'emoji', ?, 0)",
                    (user_id, emoji),
                )
                text = f"{emoji} Новое эмодзи добавлено в инвентарь"
        else:
            raise ValueError(f"неизвестный тип награды: {rtype}")

        conn.commit()
        if key_amount:
            master_winter_db.add_keys(user_id, key_amount, f"case_reward:{case_key}")
    except Exception as e:
        # Награду выдать не удалось — возвращаем кейс, чтобы он не пропал
        conn.rollback()
        master_winter_db.add_case(user_id, case_key, 1, "open_refund")
        print(f"[Winter] Ошибка выдачи награды кейса: {e}")
        return None, "❌ Не удалось выдать награду, кейс возвращён."

    return reward, text


# =========== Меню (Без изменений) =========
def main_mune():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('❄️ Зимние игры', callback_data='game'),
        types.InlineKeyboardButton('👤 Профиль', callback_data="profile"))
    
    markup.add(
        types.InlineKeyboardButton('🎁 Зимняя лавка', callback_data='shop'),
        types.InlineKeyboardButton('🎒 Инвентарь', callback_data='open_inventory'))

    markup.add(
        types.InlineKeyboardButton('🏪 Рынок', callback_data='market'),
        types.InlineKeyboardButton('⚙ Настройки', callback_data='settings_user'))
    
    markup.add(
        types.InlineKeyboardButton("❄️ Зимний сундук", callback_data="winter_chest")
    )
    
    markup.add(types.InlineKeyboardButton('📅 Календарь декабря',callback_data='master_calendar_open'))
    markup.add(types.InlineKeyboardButton('🏆 Мои титулы',callback_data='master_titles_open'),
               types.InlineKeyboardButton('📊 Лидеры',callback_data='master_leaders_open'))
    return markup

def main_mune_reply():
    """Главное меню Reply-кнопок."""
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.row(types.KeyboardButton("❄️ Зимние игры"), types.KeyboardButton("👤 Профиль"))
    markup.row(types.KeyboardButton("🎁 Зимняя лавка"), types.KeyboardButton("🎒 Инвентарь"))
    markup.row(types.KeyboardButton("🏪 Рынок"), types.KeyboardButton("❄️ Зимний сундук"))
    markup.add(types.KeyboardButton("⚙️ Настройки"))
    markup.add(types.KeyboardButton("📅 Календарь декабря"))
    markup.add(types.KeyboardButton("🏆 Мои титулы"),types.KeyboardButton("📊 Лидеры"))
    return markup


def send_main_reply_menu(chat_id, first_name=None, text=None):
    if text is None:
        text = (
            f"🎄 <b>MASTER BOT — ЗИМНИЙ СЕЗОН 2026</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👋 Добро пожаловать, <b>{html.escape(first_name or 'Игрок')}</b>!\n\n"
            f"🔮 Здесь тебя ждут зимние игры, кейсы, рынок и коллекция эмодзи.\n\n"
            f"👇 <b>Выбери раздел:</b>"
        )
    bot.send_message(chat_id, text, reply_markup=main_mune_reply(), parse_mode="HTML")

# ============ Настройки =============
def settings_menu(user_id):
    kb_type, notifications, show_id = get_user_settings(user_id)
    
    kb_text = "⌨ Переключить вид кнопок: Reply" if kb_type == 'inline' else "⌨ Переключить вид кнопок: Inline"
    notif_text = "🔔 Уведомления: ВКЛ" if notifications == 1 else "🔕 Уведомления: ВЫКЛ"
    id_text = "👁 ID для других: Виден" if show_id == 1 else "🙈 ID для других: Скрыт"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(kb_text, callback_data='toggle_kb'),
        types.InlineKeyboardButton(notif_text, callback_data='toggle_notif'),
        types.InlineKeyboardButton(id_text, callback_data='toggle_show_id'),
        types.InlineKeyboardButton('🔙 В главное меню', callback_data='back')
    )
    return markup

def profile_menu_reply():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        types.KeyboardButton("⚙ Настройки ID"),
        types.KeyboardButton("⬅ Главное меню"))
    return markup


def settings_menu_reply(user_id):
    kb_type, notifications, show_id = get_user_settings(user_id)

    kb_text = "⌨ Переключить вид кнопок: Reply" if kb_type == 'inline' else "⌨ Переключить вид кнопок: Inline"
    notif_text = "🔔 Уведомления: ВКЛ" if notifications == 1 else "🔕 Уведомления: ВЫКЛ"
    id_text = "👁 ID для других: Виден" if show_id == 1 else "🙈 ID для других: Скрыт"

    markup = types.ReplyKeyboardMarkup(row_width=1)
    markup.add(
        types.KeyboardButton(kb_text),
        types.KeyboardButton(notif_text),
        types.KeyboardButton(id_text),
        types.KeyboardButton('⬅ Главное меню')
    )
    return markup



# ============ REPLY-КЛАВИАТУРЫ ДЛЯ ОСНОВНЫХ МЕНЮ ============

def shop_mune_reply():
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton("🎁 Кейсы"), types.KeyboardButton("⭐ Магазин Stars"))
    markup.row(types.KeyboardButton("👑 Титулы за монеты"),
               types.KeyboardButton("❄️ Титулы за снежинки"))
    markup.add(types.KeyboardButton("☃️ Обмен снежков"))
    markup.add(types.KeyboardButton("⬅ Главное меню"))
    return markup

def stars_shop_menu_reply():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(types.KeyboardButton("💰 10 000 000 монет — 100 ⭐"), types.KeyboardButton("❄️ 25 снежинок — 50 ⭐"), types.KeyboardButton("⭐ Прокачать до 20 уровня — 150 ⭐"), types.KeyboardButton("⬅ Магазин"))
    return markup

def cases_menu_reply(user_id, page=0):
    case_items, page, total_pages = get_cases_page(page)
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    for case_key, c_info in case_items:
        count = get_case_count(user_id, case_key)
        icon = "❄️" if c_info["currency"] == "snowflakes" else "💰"
        markup.add(types.KeyboardButton(f"🛒 {c_info['name']} — {c_info['price']:,} {icon}".replace(",", " ")))
        markup.add(types.KeyboardButton(f"🔓 Открыть {c_info['name']} ({count} шт)"))
    nav=[]
    if page > 0: nav.append(types.KeyboardButton("⬅ Предыдущие кейсы"))
    if page < total_pages-1: nav.append(types.KeyboardButton("Следующие кейсы ➡"))
    if nav: markup.row(*nav)
    markup.add(types.KeyboardButton("⬅ Магазин"))
    return markup

def winter_chest_reply():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(types.KeyboardButton("❄️ Открыть Зимний сундук"), types.KeyboardButton("⬅ Главное меню"))
    return markup

# Кнопка эмодзи в инвентаре: «❄️ ×2 · ⚪» или «👕 ❄️ ×2 · ⚪» (если надето).
# Кружок редкости в конце необязателен: так продолжают работать и старые кнопки вида «❄️ ×2».
INV_BTN_RE = re.compile(r"^(?:👕 )?(\S+) ×(\d+)(?: · \S+)?$")

INVENTORY_EMPTY_TEXT = (
    "🎒 <b>Твой инвентарь пуст!</b>\n\n"
    "У тебя ещё нет эмодзи. Играй и открывай Зимние сундуки, чтобы собирать их."
)

def _inv_total_pages(user_id, per_page=4):
    items = [x for x in get_user_inventory(user_id) if x[0] == "emoji"]
    return max(1, (len(items) + per_page - 1) // per_page)

def inventory_reply_text(user_id, page=1):
    """Сообщение над reply-инвентарём (как в inline): пустой инвентарь — сразу «нет эмодзи»."""
    items = [x for x in get_user_inventory(user_id) if x[0] == "emoji"]
    if not items:
        return INVENTORY_EMPTY_TEXT
    per_page = 4
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    return f"🎒 <b>ТВОЙ ИНВЕНТАРЬ</b>\nСтраница {page}/{total_pages}\n\nНажми на эмодзи, чтобы открыть информацию."

def inventory_menu_reply(user_id, page=1):
    items=[x for x in get_user_inventory(user_id) if x[0]=="emoji"]; per_page=4
    total_pages=max(1,(len(items)+per_page-1)//per_page); page=min(max(1,page),total_pages)
    markup=types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for _, item_value, count in items[(page-1)*per_page:page*per_page]:
        prefix="👕 " if get_equipped_emoji(user_id)==item_value else ""
        icon=get_rarity_icon(get_emoji_rarity(item_value))
        markup.add(types.KeyboardButton(f"{prefix}{item_value} ×{count} · {icon}"))
    if page>1: markup.add(types.KeyboardButton("◀️ Предыдущая страница"))
    if page<total_pages: markup.add(types.KeyboardButton("Следующая страница ▶️"))
    markup.add(types.KeyboardButton("⬅ Главное меню"))
    return markup

def inventory_item_reply(user_id, emoji):
    rarity=get_emoji_rarity(emoji); price=get_emoji_price(emoji)
    cursor=conn.cursor(); cursor.execute("SELECT COUNT(*) FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=?",(user_id,emoji)); count=cursor.fetchone()[0]
    equipped=get_equipped_emoji(user_id)==emoji
    markup=types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(types.KeyboardButton("👕 Снять" if equipped else "👕 Одеть"), types.KeyboardButton(f"💰 Продать {emoji}"))
    markup.add(types.KeyboardButton(f"🏪 Выставить {emoji} на рынок"), types.KeyboardButton("⬅ Инвентарь"))
    text=f"<b>{emoji}</b>\n\n{get_rarity_icon(rarity)} <b>Редкость:</b> {rarity}\n💰 <b>Цена:</b> {price:,} 🪙\n📦 <b>Количество:</b> {count}".replace(","," ")
    if equipped: text += "\n\n👕 <b>Сейчас надето</b>"
    return text, markup

def market_menu_reply(user_id, page=1):
    lots,total=get_market_lots(page); total_pages=max(1,(total+MARKET_PAGE_SIZE-1)//MARKET_PAGE_SIZE); page=min(max(1,page),total_pages)
    markup=types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True); lines=["🎄 <b>ЗИМНЯЯ ЯРМАРКА ЭМОДЗИ</b> ❄️", "━━━━━━━━━━━━━━━━━━", "☃️ Покупай и продавай эмодзи за монеты!",f"🎁 Лотов: <b>{total}</b> · 📄 Страница <b>{page}/{total_pages}</b>",""]
    for lot_id,seller_id,emoji,price,username,first_name in lots:
        seller=market_seller_name(username,first_name)
        if seller_id == user_id:
            lines.append(f"#{lot_id} {emoji} — <b>{price:,} 🪙</b> — 👤 твой лот".replace(","," "))
            markup.add(types.KeyboardButton(f"❌ Снять лот #{lot_id} {emoji}"))
        else:
            lines.append(f"#{lot_id} {emoji} — <b>{price:,} 🪙</b> — {seller}".replace(","," "))
            markup.add(types.KeyboardButton(f"🛒 Купить {emoji} за {price:,} 🪙 · #{lot_id}".replace(","," ")))
    if not lots: lines.append("☃️ Ярмарка пока пуста. Выставь первый эмодзи!")
    nav=[]
    if page>1: nav.append(types.KeyboardButton("◀️ Предыдущая страница рынка"))
    if page<total_pages: nav.append(types.KeyboardButton("Следующая страница рынка ▶️"))
    if nav: markup.row(*nav)
    markup.add(types.KeyboardButton("📦 Мои лоты"),types.KeyboardButton("⬅ Главное меню"))
    return "\n".join(lines),markup

def my_market_reply(user_id):
    cursor=conn.cursor(); cursor.execute("SELECT lot_id,item_value,price FROM auction WHERE seller_id=? AND item_type='emoji' ORDER BY lot_id DESC",(user_id,)); lots=cursor.fetchall()
    markup=types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    for lot_id,emoji,price in lots: markup.add(types.KeyboardButton(f"❌ Снять лот #{lot_id} {emoji}"))
    markup.add(types.KeyboardButton("⬅ Рынок"))
    text="🎁 <b>МОИ ЛОТЫ · ЗИМНЯЯ ЯРМАРКА</b>\n━━━━━━━━━━━━━━━━━━\n\n" + ("\n".join(f"#{lot_id} {emoji} — <b>{price:,} 🪙</b>".replace(","," ") for lot_id,emoji,price in lots) if lots else "У тебя нет выставленных эмодзи.")
    return text,markup

# ============== Магазин =========
# Покупки за Telegram Stars (XTR). Цены указаны в Stars.
STARS_SHOP = {
    "coins_10m": {
        "title": "💰 10 000 000 монет",
        "description": "Пополнение игрового баланса на 10 000 000 💰",
        "stars": 100,
    },
    "snowflakes_25": {
        "title": "❄️ 25 снежинок",
        "description": "Получи 25 ❄️ снежинок для зимних кейсов",
        "stars": 50,
    },
    "level_20": {
        "title": "⭐ Уровень 20",
        "description": "Прокачка игрового уровня до 20",
        "stars": 150,
    },
}

def _fmt(n):
    return f"{int(n):,}".replace(",", " ")

# ---------- Титулы ----------
# ключ -> (название, цена в монетах)
WINTER_TITLES = {
    "snow_mage": ("❄️ Повелитель снежинок", 2500),
    "gift_hunter": ("🎁 Охотник за подарками", 5000),
    "frost_wizard": ("🧊 Ледяной маг", 10000),
    "winter_guard": ("🛷 Зимний страж", 25000),
    "snow_king": ("👑 Снежный король", 50000),
    "winter_legend": ("🌟 Легенда зимы", 100000),
    "ice_walker": ("🧊 Покоритель льдов", 18000),
    "snowball_hero": ("☃️ Снежный герой", 35000),
    "gift_keeper": ("🧸 Хранитель подарков", 65000),
    "blizzard_captain": ("🌨 Капитан метели", 120000),
    "north_star": ("⭐ Северная звезда", 200000),
}
# Уже установленные старые титулы не удаляются из базы.
PROFILE_TITLES.update({
    'pumpkin_boss': '🎁 Мастер подарков', 'crypt_master': '🗝️ Хранитель ледяного замка',
    'night_wizard': '🔮 Маг зимних чудес', 'ghost_hunter': '❄️ Охотник за снежинками',
    'witch_lord': '🎄 Повелитель праздника', 'halloween_legend': '🌟 Легенда зимы',
    'winter_2027': '🎆 Новый год 2027', 'winter_pioneer': '🏅 Первопроходец зимы',
})

# чтобы купленный титул показывался в профиле
for _key, (_name, _price) in WINTER_TITLES.items():
    PROFILE_TITLES[_key] = _name

# Титулы из кейсов доступны в «Мои титулы», а не ставятся принудительно.
CASE_WINTER_TITLES = {
    'wood': ('case_wood_title', '🪵 Снежный новичок', 9),
    'silver': ('case_silver_title', '🥈 Серебряный снежок', 7),
    'gold': ('case_gold_title', '🌟 Звезда снегопада', 6),
    'diamond': ('case_diamond_title', '💎 Ледяной кристалл', 5),
    'fire': ('case_fire_title', '🔥 Повелитель зимнего огня', 4),
    'cosmic': ('case_cosmic_title', '🌌 Страж полярной ночи', 3),
    'royal': ('case_royal_title', '👑 Корона мороза', 3),
    'void': ('case_void_title', '🪄 Мифический Новый год', 2),
}
for _case, (_key, _label, _weight) in CASE_WINTER_TITLES.items():
    PROFILE_TITLES[_key] = _label
    CASES[_case]['rewards'].append({'type': 'title', 'value': _key, 'chance': _weight})

# Старый Winter_event.db не используется и не изменяется.
# Сезон начинается с отдельной зимней базы.

# Показываем по четыре титула, чтобы клавиатура не занимала весь экран.
TITLE_CATALOG_PAGE_SIZE = 4


def _titles_page(items, page, page_size=TITLE_CATALOG_PAGE_SIZE):
    total = max(1, (len(items) + page_size - 1) // page_size)
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 0
    page = min(max(page, 0), total - 1)
    return items[page * page_size:(page + 1) * page_size], page, total


def _titles_edit_message(call, text, keyboard):
    """Повторное открытие той же страницы не должно падать с Telegram HTTP 400."""
    try:
        bot.edit_message_text(
            text, chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard, parse_mode='HTML',
        )
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def winter_titles_text(user_id, note=None, page=0):
    row = conn.execute('SELECT COALESCE(coins,0), profile_title FROM users WHERE user_id=?',
                       (user_id,)).fetchone()
    coins = int(row[0]) if row else 0
    current = PROFILE_TITLES.get(row[1] if row and row[1] else 'none', 'Без титула')
    _, page, total = _titles_page(list(WINTER_TITLES.items()), page)
    lines = [
        '👑 <b>ЗИМНЯЯ ЛАВКА · ТИТУЛЫ ЗА МОНЕТЫ</b>',
        '━━━━━━━━━━━━━━━━━━',
        f'💰 Баланс: <b>{_fmt(coins)}</b> 🪙',
        f'🎖 Твой титул: <b>{html.escape(current)}</b>',
        f'📖 Страница <b>{page + 1}/{total}</b>',
    ]
    if note:
        lines.append('\n' + html.escape(str(note)))
    lines.append('\nВыбери титул:')
    return '\n'.join(lines)


def winter_titles_menu(page=0, user_id=None):
    current, page, total = _titles_page(list(WINTER_TITLES.items()), page)
    owned = _master_owned_titles(user_id) if user_id is not None else {}
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key, (name, price) in current:
        label = f'✅ {name} · получен' if key in owned else f'{name} — {_fmt(price)} 🪙'
        markup.add(types.InlineKeyboardButton(label, callback_data=f'buy_title_{key}'))
    navigation = []
    if page > 0:
        navigation.append(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'coin_titles_page:{page-1}'))
    navigation.append(types.InlineKeyboardButton(f'📄 {page+1}/{total}', callback_data=f'coin_titles_page:{page}'))
    if page < total-1:
        navigation.append(types.InlineKeyboardButton('Вперёд ➡️', callback_data=f'coin_titles_page:{page+1}'))
    markup.row(*navigation)
    markup.add(types.InlineKeyboardButton('🛍 В зимнюю лавку', callback_data='shop'))
    return markup


def winter_titles_reply(user_id=None, page=0):
    """В Reply-режиме каталог листается Inline-кнопками внутри сообщения."""
    return winter_titles_menu(page, user_id)

def buy_winter_title(user_id, title_key):
    """Покупка титула за монеты: деньги и титул сохраняются одной транзакцией."""
    item = WINTER_TITLES.get(title_key)
    if not item:
        return False, '❌ Такой титул не найден.'
    name, price = item
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute('ATTACH DATABASE ? AS winter', (master_winter_db.get_db_path(),))
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT COALESCE(coins,0), profile_title FROM users WHERE user_id=?',
                         (user_id,)).fetchone()
        if not row:
            return False, '❌ Профиль не найден.'
        if (row[1] == title_key or
                db.execute('SELECT 1 FROM winter.titles WHERE user_id=? AND title_key=?',
                           (user_id, title_key)).fetchone()):
            return False, f'✅ Титул {name} уже получен. Выбери его в «Мои титулы».'
        if row[0] < price:
            return False, f'❌ Не хватает монет. Нужно {_fmt(price)} 🪙.'
        updated = db.execute('UPDATE users SET coins=coins-?, profile_title=? '
                             'WHERE user_id=? AND COALESCE(coins,0)>=?',
                             (price, title_key, user_id, price))
        if updated.rowcount != 1:
            return False, '❌ Не удалось купить титул.'
        stamp = datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO winter.titles(user_id,title_key,price,bought_at) VALUES(?,?,?,?)',
                   (user_id, title_key, price, stamp))
        db.execute('INSERT INTO winter.event_log(ts,user_id,action,amount,detail) '
                   'VALUES(?,?,?,?,?)',
                   (stamp, user_id, 'title', price, title_key))
    return True, f'🏆 Титул {name} куплен и установлен!'

def _winter_shop_front(user_id):
    """Праздничная витрина; не меняет баланс и логику покупок."""
    row = conn.execute('SELECT COALESCE(coins,0) FROM users WHERE user_id=?', (user_id,)).fetchone()
    coins = int(row[0]) if row else 0
    try:
        snow = int(get_snowflakes(user_id))
    except Exception:
        snow = 0
    return ("🎄 <b>MASTER BOT · ЗИМНЯЯ ЛАВКА</b> 🎁\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "☃️ Добро пожаловать в праздничный магазин!\n"
            "❄️ Здесь тебя ждут кейсы, коллекционные титулы и зимние находки.\n\n"
            f"💰 Монеты: <b>{_fmt(coins)}</b>\n"
            f"❄️ Снежинки: <b>{_fmt(snow)}</b>\n"
            f"☃️ Снежки: <b>{_fmt(get_snowballs(user_id))}</b>\n\n"
            "✨ Выбери раздел ниже и найди себе подарок!")


def shop_mune():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🎁 Праздничные кейсы", callback_data="cases"),
        types.InlineKeyboardButton('⭐ Валюта за Stars', callback_data="buy_coins")
    )
    markup.add(
        types.InlineKeyboardButton("👑 Титулы за монеты", callback_data="winter_titles"),
        types.InlineKeyboardButton('❄️ Титулы за снежинки', callback_data='master_snow_shop')
    )
    markup.add(types.InlineKeyboardButton('☃️ Обмен снежков', callback_data='master_snowballs_exchange'))
    markup.add(
        types.InlineKeyboardButton('🏠 Главное меню', callback_data='back')
    )
    return markup

def stars_shop_menu():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton('💰 10 000 000 монет — 100 ⭐', callback_data='stars_buy_coins_10m'),
        types.InlineKeyboardButton('❄️ 25 снежинок — 50 ⭐', callback_data='stars_buy_keys_5'),
        types.InlineKeyboardButton('⭐ Прокачать до 20 уровня — 150 ⭐', callback_data='stars_buy_level_20'),
        types.InlineKeyboardButton('⬅ Назад', callback_data='shop')
    )
    return markup

def _send_stars_invoice(chat_id, user_id, product_key):
    product = STARS_SHOP[product_key]
    payload = f"stars:{product_key}:{user_id}"

    invoice_msg = bot.send_invoice(
        chat_id=chat_id,
        title=product["title"],
        description=product["description"],
        invoice_payload=payload,
        provider_token="",
        currency="XTR",
        prices=[
            types.LabeledPrice(
                label=product["title"],
                amount=product["stars"]
            )
        ],
        start_parameter=f"buy_{product_key}",
    )

    cancel_markup = types.InlineKeyboardMarkup()
    cancel_markup.add(
        types.InlineKeyboardButton(
            "❌ Отменить оплату",
            callback_data=f"cancel_stars_{invoice_msg.message_id}"
        )
    )

    bot.send_message(
        chat_id,
        "💳 <b>Оплата создана</b>\n\n"
        "Если передумал покупать, нажми кнопку ниже.",
        reply_markup=cancel_markup,
        parse_mode="HTML"
    )


def send_stars_invoice(call, product_key):
    """Счёт на оплату Stars после нажатия inline-кнопки."""
    _send_stars_invoice(call.message.chat.id, call.from_user.id, product_key)


def send_stars_invoice_from_message(message, product_key):
    """Счёт на оплату Stars после нажатия reply-кнопки."""
    _send_stars_invoice(message.chat.id, message.from_user.id, product_key)

# ============== Игры ============
# По две кнопки на строку. Игрок видит замок и причину; персонал может тестировать.
MASTER_GAME_BUTTONS = {
    'ttt': '❌⭕ Крестики-нолики',
    'maze': '🧊 Лабиринт',
    'lights': '🎄 Гирлянды',
    'penguin': '🐧 Экспедиция',
    'cipher': '🔔 Морозный шифр',
    'aurora': '🌌 Сияние',
    'express': '🚂 Снежный экспресс',
    'fortress': '🏰 Крепость',
}

MASTER_UPCOMING_GAMES = {
    'aurora': '🌌 Полярное сияние',
    'express': '🚂 Снежный экспресс',
    'fortress': '🏰 Ледяная крепость',
}


def _master_game_buttons(user_id=None):
    """Игрок видит замок; сотрудник может открыть закрытую игру для тестирования."""
    staff = user_id is not None and _master_staff(user_id)
    items = []
    for key, (name, callback) in _master_feature_catalog().items():
        label = MASTER_GAME_BUTTONS.get(key, name)
        enabled = _master_feature_enabled(key)
        visible = label if enabled else ('🧪 ' + label if staff else '🔒 ' + label)
        items.append((visible, callback if enabled or staff else 'mnew:locked:' + key))
    return items


def game_mune(user_id=None):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    buttons = _master_game_buttons(user_id)
    for index in range(0, len(buttons), 2):
        keyboard.row(*(types.InlineKeyboardButton(label, callback_data=data)
                       for label, data in buttons[index:index + 2]))
    keyboard.add(types.InlineKeyboardButton('🏠 Главное меню', callback_data='back'))
    return keyboard


def game_mune_reply(user_id=None):
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    buttons = _master_game_buttons(user_id)
    for index in range(0, len(buttons), 2):
        keyboard.row(*(types.KeyboardButton(label)
                       for label, _ in buttons[index:index + 2]))
    keyboard.add(types.KeyboardButton('⬅ Главное меню'))
    return keyboard


def _master_games_menu_text():
    return ('❄️ <b>ЗИМНИЕ ИГРЫ</b>\n'
            '━━━━━━━━━━━━━━━━━━\n'
            '🎮 Выбери игру.\n'
            '🔒 Нажми на закрытую игру, чтобы увидеть причину.')

def _games_menu_markup(user_id):
    """Меню игр под текущий вид кнопок игрока (reply или inline)."""
    kb_type = get_user_settings(user_id)[0]
    return game_mune_reply(user_id) if kb_type == 'reply' else game_mune(user_id)


def finish_inline_game(call, result_text):
    """Конец игры в Inline-режиме: показываем итог и сразу возвращаем в меню игр."""
    try:
        bot.edit_message_text(
            f"{result_text}\n\n{_master_games_menu_text()}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=game_mune(call.from_user.id),
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"[GAME] Не удалось показать меню игр: {e}")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

def game_action_reply(action=None):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    if action == "witch":
        markup.add(types.KeyboardButton("🛑 Выйти из игры"), types.KeyboardButton("⬅ Игры"))
    elif action == "ttt":
        buttons = [types.KeyboardButton(str(i)) for i in range(1, 10)]
        markup.row(buttons[0], buttons[1], buttons[2])
        markup.row(buttons[3], buttons[4], buttons[5])
        markup.row(buttons[6], buttons[7], buttons[8])
        markup.add(types.KeyboardButton("🛑 Выйти из игры"))
    elif action == "duel":
        markup.add(types.KeyboardButton("⚔️ Атака"), types.KeyboardButton("🛡️ Щит"))
        markup.add(types.KeyboardButton("🧪 Зелье"), types.KeyboardButton("🏃 Сбежать"))
    elif action == "trick":
        markup.row(
            types.KeyboardButton("🎁 Подарок 1"),
            types.KeyboardButton("🎁 Подарок 2"),
            types.KeyboardButton("🎁 Подарок 3"),
        )
        markup.add(types.KeyboardButton("🛑 Выйти из игры"))
    elif action == "graveyard":
        markup.add(types.KeyboardButton("🔎 Искать"), types.KeyboardButton("🛑 Выйти из игры"))
    else:
        markup.add(types.KeyboardButton("⬅ Игры"), types.KeyboardButton("⬅ Главное меню"))
    return markup


# ============= Коллекция эмоджи: редкость и цена =============
EMOJI_RARITY = {
    "❄️": "Обычная", "🦴": "Обычная", "🔮": "Обычная", "📜": "Обычная", "⛄": "Обычная",
    "🩸": "Редкая", "❄️": "Очень редкая", "😈": "Эпическая", "🕷️": "Мифическая", "👁️": "Легендарная",
}

EMOJI_DATA = {

    # ⚪ Обычные — 1 000–5 000
    "😀": {"rarity": "Обычная", "price": 1000},
    "😃": {"rarity": "Обычная", "price": 1138},
    "😄": {"rarity": "Обычная", "price": 1276},
    "😁": {"rarity": "Обычная", "price": 1414},
    "😆": {"rarity": "Обычная", "price": 1552},
    "😅": {"rarity": "Обычная", "price": 1690},
    "😂": {"rarity": "Обычная", "price": 1828},
    "🤣": {"rarity": "Обычная", "price": 1966},
    "😊": {"rarity": "Обычная", "price": 2103},
    "🙂": {"rarity": "Обычная", "price": 2241},
    "🙃": {"rarity": "Обычная", "price": 2379},
    "😉": {"rarity": "Обычная", "price": 2517},
    "😌": {"rarity": "Обычная", "price": 2655},
    "😇": {"rarity": "Обычная", "price": 2793},
    "🥰": {"rarity": "Обычная", "price": 2931},
    "😍": {"rarity": "Обычная", "price": 3069},
    "🤗": {"rarity": "Обычная", "price": 3207},
    "🤔": {"rarity": "Обычная", "price": 3345},
    "😐": {"rarity": "Обычная", "price": 3483},
    "😑": {"rarity": "Обычная", "price": 3621},
    "😶": {"rarity": "Обычная", "price": 3759},
    "🙄": {"rarity": "Обычная", "price": 3897},
    "😏": {"rarity": "Обычная", "price": 4034},
    "😴": {"rarity": "Обычная", "price": 4172},
    "🤓": {"rarity": "Обычная", "price": 4310},
    "😎": {"rarity": "Обычная", "price": 4448},
    "🤪": {"rarity": "Обычная", "price": 4586},
    "😜": {"rarity": "Обычная", "price": 4724},
    "😝": {"rarity": "Обычная", "price": 4862},
    "😛": {"rarity": "Обычная", "price": 5000},

    # 🟢 Необычные — 7 500–25 000
    "🤩": {"rarity": "Необычная", "price": 7500},
    "🥳": {"rarity": "Необычная", "price": 8103},
    "🥶": {"rarity": "Необычная", "price": 8707},
    "🥵": {"rarity": "Необычная", "price": 9310},
    "🤯": {"rarity": "Необычная", "price": 9914},
    "😱": {"rarity": "Необычная", "price": 10517},
    "😡": {"rarity": "Необычная", "price": 11121},
    "🤬": {"rarity": "Необычная", "price": 11724},
    "🥺": {"rarity": "Необычная", "price": 12328},
    "😭": {"rarity": "Необычная", "price": 12931},
    "😤": {"rarity": "Необычная", "price": 13534},
    "🫡": {"rarity": "Необычная", "price": 14138},
    "🫠": {"rarity": "Необычная", "price": 14741},
    "🫣": {"rarity": "Необычная", "price": 15345},
    "🫢": {"rarity": "Необычная", "price": 15948},
    "🤫": {"rarity": "Необычная", "price": 16552},
    "🤭": {"rarity": "Необычная", "price": 17155},
    "🧐": {"rarity": "Необычная", "price": 17759},
    "🤠": {"rarity": "Необычная", "price": 18362},
    "😮": {"rarity": "Необычная", "price": 18966},
    "😲": {"rarity": "Необычная", "price": 19569},
    "😳": {"rarity": "Необычная", "price": 20172},
    "😨": {"rarity": "Необычная", "price": 20776},
    "😰": {"rarity": "Необычная", "price": 21379},
    "😥": {"rarity": "Необычная", "price": 21983},
    "😓": {"rarity": "Необычная", "price": 22586},
    "😞": {"rarity": "Необычная", "price": 23190},
    "😔": {"rarity": "Необычная", "price": 23793},
    "😟": {"rarity": "Необычная", "price": 24397},
    "😬": {"rarity": "Необычная", "price": 25000},

    # 🔵 Редкие — 35 000–100 000
    "⛄": {"rarity": "Редкая", "price": 35000},
    "👽": {"rarity": "Редкая", "price": 36912},
    "🤖": {"rarity": "Редкая", "price": 38824},
    "🎄": {"rarity": "Редкая", "price": 40735},
    "😺": {"rarity": "Редкая", "price": 42647},
    "😸": {"rarity": "Редкая", "price": 44559},
    "😹": {"rarity": "Редкая", "price": 46471},
    "😻": {"rarity": "Редкая", "price": 48382},
    "😼": {"rarity": "Редкая", "price": 50294},
    "🙀": {"rarity": "Редкая", "price": 52206},
    "😿": {"rarity": "Редкая", "price": 54118},
    "😾": {"rarity": "Редкая", "price": 56029},
    "🐶": {"rarity": "Редкая", "price": 57941},
    "🐱": {"rarity": "Редкая", "price": 59853},
    "🦊": {"rarity": "Редкая", "price": 61765},
    "🐻": {"rarity": "Редкая", "price": 63676},
    "🐼": {"rarity": "Редкая", "price": 65588},
    "🐨": {"rarity": "Редкая", "price": 67500},
    "🐯": {"rarity": "Редкая", "price": 69412},
    "🦁": {"rarity": "Редкая", "price": 71324},
    "🐵": {"rarity": "Редкая", "price": 73235},
    "🙈": {"rarity": "Редкая", "price": 75147},
    "🙉": {"rarity": "Редкая", "price": 77059},
    "🙊": {"rarity": "Редкая", "price": 78971},
    "🐸": {"rarity": "Редкая", "price": 80882},
    "🐷": {"rarity": "Редкая", "price": 82794},
    "🐮": {"rarity": "Редкая", "price": 84706},
    "🐹": {"rarity": "Редкая", "price": 86618},
    "🐰": {"rarity": "Редкая", "price": 88529},
    "🐭": {"rarity": "Редкая", "price": 90441},
    "🐲": {"rarity": "Редкая", "price": 92353},
    "🦋": {"rarity": "Редкая", "price": 94265},
    "🐝": {"rarity": "Редкая", "price": 96176},
    "🦂": {"rarity": "Редкая", "price": 98088},
    "🐙": {"rarity": "Редкая", "price": 100000},

    # 🟦 Очень редкие — 125 000–300 000
    "🦈": {"rarity": "Очень редкая", "price": 125000},
    "🐊": {"rarity": "Очень редкая", "price": 130303},
    "🦖": {"rarity": "Очень редкая", "price": 135606},
    "🦕": {"rarity": "Очень редкая", "price": 140909},
    "🌈": {"rarity": "Очень редкая", "price": 146212},
    "🌙": {"rarity": "Очень редкая", "price": 151515},
    "⭐": {"rarity": "Очень редкая", "price": 156818},
    "🌟": {"rarity": "Очень редкая", "price": 162121},
    "✨": {"rarity": "Очень редкая", "price": 167424},
    "⚡": {"rarity": "Очень редкая", "price": 172727},
    "🔥": {"rarity": "Очень редкая", "price": 178030},
    "❄️": {"rarity": "Очень редкая", "price": 183333},
    "🌊": {"rarity": "Очень редкая", "price": 188636},
    "☄️": {"rarity": "Очень редкая", "price": 193939},
    "☀️": {"rarity": "Очень редкая", "price": 199242},
    "🌑": {"rarity": "Очень редкая", "price": 204545},
    "🌕": {"rarity": "Очень редкая", "price": 209848},
    "🌞": {"rarity": "Очень редкая", "price": 215152},
    "🌚": {"rarity": "Очень редкая", "price": 220455},
    "🌍": {"rarity": "Очень редкая", "price": 225758},
    "🌎": {"rarity": "Очень редкая", "price": 231061},
    "🌏": {"rarity": "Очень редкая", "price": 236364},
    "🌀": {"rarity": "Очень редкая", "price": 241667},
    "🌪️": {"rarity": "Очень редкая", "price": 246970},
    "🌋": {"rarity": "Очень редкая", "price": 252273},
    "🌠": {"rarity": "Очень редкая", "price": 257576},
    "🪐": {"rarity": "Очень редкая", "price": 262879},
    "🍀": {"rarity": "Очень редкая", "price": 268182},
    "🌸": {"rarity": "Очень редкая", "price": 273485},
    "🌺": {"rarity": "Очень редкая", "price": 278788},
    "🌹": {"rarity": "Очень редкая", "price": 284091},
    "🌻": {"rarity": "Очень редкая", "price": 289394},
    "🍁": {"rarity": "Очень редкая", "price": 294697},
    "🍂": {"rarity": "Очень редкая", "price": 300000},

    # 🟣 Эпические — 400 000–800 000
    "👑": {"rarity": "Эпическая", "price": 400000},
    "💎": {"rarity": "Эпическая", "price": 411765},
    "💠": {"rarity": "Эпическая", "price": 423529},
    "🔮": {"rarity": "Эпическая", "price": 435294},
    "🪄": {"rarity": "Эпическая", "price": 447059},
    "🧿": {"rarity": "Эпическая", "price": 458824},
    "⚜️": {"rarity": "Эпическая", "price": 470588},
    "🏆": {"rarity": "Эпическая", "price": 482353},
    "🥇": {"rarity": "Эпическая", "price": 494118},
    "🎖️": {"rarity": "Эпическая", "price": 505882},
    "💰": {"rarity": "Эпическая", "price": 517647},
    "💵": {"rarity": "Эпическая", "price": 529412},
    "🤑": {"rarity": "Эпическая", "price": 541176},
    "🎰": {"rarity": "Эпическая", "price": 552941},
    "🎲": {"rarity": "Эпическая", "price": 564706},
    "🃏": {"rarity": "Эпическая", "price": 576471},
    "♠️": {"rarity": "Эпическая", "price": 588235},
    "♥️": {"rarity": "Эпическая", "price": 600000},
    "♦️": {"rarity": "Эпическая", "price": 611765},
    "♣️": {"rarity": "Эпическая", "price": 623529},
    "🗡️": {"rarity": "Эпическая", "price": 635294},
    "⚔️": {"rarity": "Эпическая", "price": 647059},
    "🛡️": {"rarity": "Эпическая", "price": 658824},
    "🏹": {"rarity": "Эпическая", "price": 670588},
    "🔱": {"rarity": "Эпическая", "price": 682353},
    "🪓": {"rarity": "Эпическая", "price": 694118},
    "🔨": {"rarity": "Эпическая", "price": 705882},
    "💣": {"rarity": "Эпическая", "price": 717647},
    "🧨": {"rarity": "Эпическая", "price": 729412},
    "🚀": {"rarity": "Эпическая", "price": 741176},
    "🛸": {"rarity": "Эпическая", "price": 752941},
    "🌌": {"rarity": "Эпическая", "price": 764706},
    "🏰": {"rarity": "Эпическая", "price": 776471},
    "🏯": {"rarity": "Эпическая", "price": 788235},
    "🗿": {"rarity": "Эпическая", "price": 800000},

    # 🟠 Легендарные — 1 000 000–2 500 000
    "🐉": {"rarity": "Легендарная", "price": 1000000},
    "🦅": {"rarity": "Легендарная", "price": 1078947},
    "🦚": {"rarity": "Легендарная", "price": 1157895},
    "🦄": {"rarity": "Легендарная", "price": 1236842},
    "🧞": {"rarity": "Легендарная", "price": 1315789},
    "🧜": {"rarity": "Легендарная", "price": 1394737},
    "🧚": {"rarity": "Легендарная", "price": 1473684},
    "🧝": {"rarity": "Легендарная", "price": 1552632},
    "🧟": {"rarity": "Легендарная", "price": 1631579},
    "👹": {"rarity": "Легендарная", "price": 1710526},
    "👺": {"rarity": "Легендарная", "price": 1789474},
    "👿": {"rarity": "Легендарная", "price": 1868421},
    "😈": {"rarity": "Легендарная", "price": 1947368},
    "❄️": {"rarity": "Легендарная", "price": 2026316},
    "❄️": {"rarity": "Легендарная", "price": 2105263},
    "👁️": {"rarity": "Легендарная", "price": 2184211},
    "👁️‍🗨️": {"rarity": "Легендарная", "price": 2263158},
    "🕳️": {"rarity": "Легендарная", "price": 2342105},
    "🩸": {"rarity": "Легендарная", "price": 2421053},
    "🖤": {"rarity": "Легендарная", "price": 2500000},

    # 🔴 Мифические — 3 000 000–5 000 000
    "❤️": {"rarity": "Мифическая", "price": 3000000},
    "🧡": {"rarity": "Мифическая", "price": 3142857},
    "💛": {"rarity": "Мифическая", "price": 3285714},
    "💚": {"rarity": "Мифическая", "price": 3428571},
    "💙": {"rarity": "Мифическая", "price": 3571429},
    "💜": {"rarity": "Мифическая", "price": 3714286},
    "🩷": {"rarity": "Мифическая", "price": 3857143},
    "🩵": {"rarity": "Мифическая", "price": 4000000},
    "🤍": {"rarity": "Мифическая", "price": 4142857},
    "🤎": {"rarity": "Мифическая", "price": 4285714},
    "💖": {"rarity": "Мифическая", "price": 4428571},
    "💗": {"rarity": "Мифическая", "price": 4571429},
    "💓": {"rarity": "Мифическая", "price": 4714286},
    "💞": {"rarity": "Мифическая", "price": 4857143},
    "💕": {"rarity": "Мифическая", "price": 5000000},
}

RARITY_ICONS = {
    "Обычная": "⚪",
    "Необычная": "🟢",
    "Редкая": "🔵",
    "Очень редкая": "💠",
    "Эпическая": "🟣",
    "Легендарная": "🟠",
    "Мифическая": "🔴",
}

def get_emoji_rarity(emoji):
    return EMOJI_DATA.get(emoji, {"rarity": "Обычная", "price": 50})["rarity"]

def get_emoji_price(emoji):
    return EMOJI_DATA.get(emoji, {"rarity": "Обычная", "price": 50})["price"]

def get_rarity_icon(rarity):
    return RARITY_ICONS.get(rarity, "⚪")

def show_emoji_card(chat_id, message_id, user_id, emoji):
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_type = 'emoji' AND item_value = ?", (user_id, emoji))
    count = cursor.fetchone()[0]
    if count <= 0:
        bot.send_message(chat_id, "❌ Эмоджи больше нет в инвентаре!")
        return
    rarity = get_emoji_rarity(emoji); price = get_emoji_price(emoji); equipped = get_equipped_emoji(user_id) == emoji
    text = f"<b>{emoji}</b>\n\n{get_rarity_icon(rarity)} <b>Редкость:</b> {rarity}\n💰 <b>Цена:</b> {price} 🪙\n📦 <b>Количество:</b> {count}\n"
    if equipped: text += "\n👕 <b>Сейчас надето</b>\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("👕 Снять" if equipped else "👕 Одеть", callback_data=f"emoji_unequip_{emoji}" if equipped else f"emoji_equip_{emoji}"))
    markup.add(types.InlineKeyboardButton(f"💰 Продать за {price} 🪙", callback_data=f"emoji_sell_{emoji}"))
    markup.add(types.InlineKeyboardButton("🏪 На рынок", callback_data=f"emoji_market_{emoji}"))
    markup.add(types.InlineKeyboardButton("🔙 В инвентарь", callback_data="open_inventory"))
    bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode="HTML")

pending_market = {}

# ============= 🏪 РЫНОК ЭМОДЗИ =============
MARKET_PAGE_SIZE = 5


def get_market_lots(page=1):
    """Возвращает лоты рынка и общее количество лотов."""
    page = max(1, int(page))
    offset = (page - 1) * MARKET_PAGE_SIZE
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM auction WHERE item_type = 'emoji' AND price > 0")
    total = cursor.fetchone()[0]

    cursor.execute("""
        SELECT a.lot_id, a.seller_id, a.item_value, a.price,
               COALESCE(u.username, ''), COALESCE(u.first_name, 'Игрок')
        FROM auction a
        LEFT JOIN users u ON u.user_id = a.seller_id
        WHERE a.item_type = 'emoji' AND a.price > 0
        ORDER BY a.lot_id DESC
        LIMIT ? OFFSET ?
    """, (MARKET_PAGE_SIZE, offset))

    return cursor.fetchall(), total


def market_seller_name(username, first_name):
    if username:
        return '@' + username.lstrip('@')
    return first_name or 'Игрок'


def notify_market_sale(seller_id, buyer_id, emoji, price):
    """Пишет продавцу, что его лот купили. Только если у продавца включены уведомления."""
    try:
        if not get_user_settings(seller_id)[1]:      # 0 = уведомления выключены
            return False
        cursor = conn.cursor()
        cursor.execute("SELECT username, first_name FROM users WHERE user_id = ?", (buyer_id,))
        row = cursor.fetchone()
        buyer = market_seller_name(row[0], row[1]) if row else "Игрок"
        bot.send_message(
            seller_id,
            f"🏪 <b>Твой лот продан!</b>\n\n"
            f"{emoji} куплен игроком {html.escape(buyer)}\n"
            f"💰 Ты получил: <b>{_fmt(price)}</b> 🪙",
            parse_mode="HTML"
        )
        return True
    except Exception as e:
        print(f"[MARKET NOTIFY ERROR] {e}")
        return False


def show_market(chat_id, message_id, user_id, page=1):
    lots, total = get_market_lots(page)
    total_pages = max(1, (total + MARKET_PAGE_SIZE - 1) // MARKET_PAGE_SIZE)
    page = min(max(1, page), total_pages)

    # Если страница оказалась пустой после удаления последнего лота — показываем последнюю.
    if not lots and page > 1:
        page -= 1
        lots, total = get_market_lots(page)
        total_pages = max(1, (total + MARKET_PAGE_SIZE - 1) // MARKET_PAGE_SIZE)

    cursor = conn.cursor()
    cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    text = (
        "🎄 <b>ЗИМНЯЯ ЯРМАРКА ЭМОДЗИ</b> ❄️\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "☃️ Покупай, продавай и собирай редкие эмодзи!\n"
        f"💰 Твой баланс: <b>{_fmt(balance)}</b> 🪙\n"
        f"🎁 Активных лотов: <b>{total}</b> · 📄 {page}/{total_pages}\n\n"
    )

    markup = types.InlineKeyboardMarkup(row_width=1)

    if not lots:
        text += "☃️ <b>На ярмарке пока тихо.</b>\n\n"
        text += "🎁 Выставь первый эмодзи из инвентаря и открой праздничную торговлю!"
    else:
        for lot_id, seller_id, emoji, price, username, first_name in lots:
            rarity = get_emoji_rarity(emoji)
            icon = get_rarity_icon(rarity)
            seller = market_seller_name(username, first_name)

            text += (
                f"{emoji} <b>{price}</b> 🪙\n"
                f"{icon} {rarity} • 👤 {seller}\n\n"
            )

            if seller_id == user_id:
                markup.add(
                    types.InlineKeyboardButton(
                        f"❌ Снять {emoji} • {price} 🪙",
                        callback_data=f"market_cancel_{lot_id}"
                    )
                )
            else:
                markup.add(
                    types.InlineKeyboardButton(
                        f"🛒 Купить {emoji} • {price} 🪙",
                        callback_data=f"market_buy_{lot_id}"
                    )
                )

    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"market_page_{page - 1}"))
    nav.append(types.InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="ignore_click"))
    if page < total_pages:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"market_page_{page + 1}"))
    markup.row(*nav)

    markup.add(
        types.InlineKeyboardButton("📦 Мои лоты", callback_data="market_my"),
        types.InlineKeyboardButton("🔄 Обновить", callback_data=f"market_page_{page}")
    )
    markup.add(types.InlineKeyboardButton("🔙 В меню", callback_data="back"))

    try:
        bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )
    except Exception:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")


def show_my_market_lots(chat_id, message_id, user_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT lot_id, item_value, price
        FROM auction
        WHERE seller_id = ? AND item_type = 'emoji'
        ORDER BY lot_id DESC
    """, (user_id,))
    lots = cursor.fetchall()

    text = "🎁 <b>МОИ ЛОТЫ · ЗИМНЯЯ ЯРМАРКА</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)

    if not lots:
        text += "📭 У тебя нет выставленных лотов."
    else:
        for lot_id, emoji, price in lots:
            rarity = get_emoji_rarity(emoji)
            text += f"{emoji} • {rarity} • <b>{price}</b> 🪙\n"
            markup.add(types.InlineKeyboardButton(
                f"❌ Снять {emoji} • {price} 🪙",
                callback_data=f"market_cancel_{lot_id}"
            ))

    markup.add(types.InlineKeyboardButton("🏪 Назад на рынок", callback_data="market"))
    markup.add(types.InlineKeyboardButton("🔙 В меню", callback_data="back"))

    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=markup,
        parse_mode="HTML"
    )

# ============= Пользователь & Пагинация Инвентаря =========.
def get_user_inventory(user_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT item_type, item_value, COUNT(*) 
        FROM inventory 
        WHERE user_id = ? 
        GROUP BY item_type, item_value
    """, (user_id,))
    return cursor.fetchall()

def show_inventory_ui(chat_id, message_id, user_id, page=1):
    items = [x for x in get_user_inventory(user_id) if x[0] == 'emoji']
    per_page = 4; total_pages = max(1, (len(items)+per_page-1)//per_page); page=min(max(1,page),total_pages)
    page_items=items[(page-1)*per_page:page*per_page]
    markup=types.InlineKeyboardMarkup(row_width=2)
    if items:
        text=f"🎒 <b>ТВОЙ ИНВЕНТАРЬ</b>\nСтраница {page}/{total_pages}\n\nНажми на эмоджи, чтобы открыть информацию."
        for _, item_value, count in page_items:
            rarity=get_emoji_rarity(item_value); prefix="👕 " if get_equipped_emoji(user_id)==item_value else ""
            markup.add(types.InlineKeyboardButton(f"{prefix}{item_value} ×{count} · {get_rarity_icon(rarity)}", callback_data=f"emoji_info_{item_value}"))
        nav=[]
        if page>1: nav.append(types.InlineKeyboardButton("◀️",callback_data=f"invpage_{page-1}"))
        nav.append(types.InlineKeyboardButton(f"📄 {page}/{total_pages}",callback_data="ignore_click"))
        if page<total_pages: nav.append(types.InlineKeyboardButton("▶️",callback_data=f"invpage_{page+1}"))
        markup.row(*nav)
    else: text=INVENTORY_EMPTY_TEXT
    markup.add(types.InlineKeyboardButton('⬅️ Назад',callback_data='back'))
    try: bot.edit_message_text(text,chat_id=chat_id,message_id=message_id,reply_markup=markup,parse_mode="HTML")
    except Exception: pass

# ============ Обработчики команд ==============
@bot.message_handler(commands=['start'])
def start_bot(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username
    reason = check_ban(user_id)

    if openbot.is_globally_banned(user_id):
        bot.reply_to(
            message,
            "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети."
        )
        return

    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return

    openbot.create_id(user_id, first_name)
    openbot.register_bot_activity(user_id, "Master_bot")
    create_user(user_id, username, first_name)

    # Проверяем, какой тип клавиатуры выбрал пользователь
    kb_type, notifications, show_id = get_user_settings(user_id)

    if kb_type == "reply":
        # Обычные кнопки снизу
        send_main_reply_menu(
            message.chat.id,
            first_name,
            (
                f"🎄 <b>MASTER BOT — ЗИМНИЙ СЕЗОН 2026</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👋 Привет, <b>{html.escape(first_name)}</b>!\n\n"
                f"❄️ Добро пожаловать в зимний Master Bot!\n"
                f"🎁 Открывай кейсы • 🪙 торгуй • ❄️ играй • 💎 собирай редкие эмодзи\n\n"
                f"👇 <b>Главное меню:</b>"
            )
        )

    else:
        # Inline-кнопки
        bot.send_message(
            message.chat.id,
            f"🎄 <b>MASTER BOT — ЗИМНИЙ СЕЗОН 2026</b>\n\n❄️ Привет, {html.escape(first_name or 'Игрок')}!\n🎮 Выбирай зимние игры и получай награды.\n🎁 Праздничный календарь: /calendar",
            parse_mode="HTML",
            reply_markup=main_mune()
        )

def _master_help_role(uid):
    try:
        info = openbot.get_user(uid)
        if isinstance(info, dict) and info.get('exists'):
            role = str(info.get('status', 'user')).lower()
            if role in {'user','coder','tester','admin','developer','owner'}:
                return role
    except Exception:
        pass
    return 'user'


# Replaced old handler bot_help with Winter command parity implementation.

@bot.message_handler(commands=['game_stop'])
def stop_game(message):
    user_id = message.from_user.id
    _mnew_clear(user_id)
    game_data = get_active_game(user_id)
    if game_data and game_data[0] != 'none':
        game_name = game_data[0].upper()
        delete_active_game(user_id)
        msg = bot.send_message(message.chat.id, f"🛑 Игра «{game_name}» остановлена. Вы вернулись в главное меню.")
        time.sleep(1)
        bot.edit_message_text(f'👋 Привет, {message.from_user.first_name}\nДобро пожаловать!', reply_markup=main_mune(), 
            message_id=msg.message_id, 
            chat_id=message.chat.id)
    else:
        bot.send_message(message.chat.id, "Ты сейчас ни во что не играешь.")


# Replaced old handler ban_user with Winter command parity implementation.
        

# Replaced old handler unban_by_username with Winter command parity implementation.

@bot.message_handler(commands=['sendall'])
def send_all(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 1):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 1 ур.")
        return
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    text = message.text.replace("/sendall", "").strip()
    if not text:
        bot.reply_to(message, "❌ Введи текст для рассылки.\nПример: `/sendall Привет!`", parse_mode="Markdown")
        return
        
    cursor = conn.cursor()
    # Берем только тех, у кого включены уведомления (notifications = 1)
    cursor.execute("SELECT user_id FROM users WHERE notifications = 1")
    users = cursor.fetchall()
    
    bot.send_message(message.chat.id, f"🚀 Рассылка запущена...\nВсего получателей: {len(users)}")
    success = blocked = errors = 0
    
    for user in users:
        target_id = user[0]
        try:
            bot.send_message(target_id, text, parse_mode="HTML")
            success += 1
        except apihelper.ApiTelegramException as e:
            if "bot was blocked by the user" in e.description:
                blocked += 1
                # Отключаем уведомления в БД, чтобы больше не пытаться ему писать
                cursor.execute("UPDATE users SET notifications = 0 WHERE user_id = ?", (target_id,))
                conn.commit()
            else:
                errors += 1
        except Exception:
            errors += 1
            
    bot.send_message(message.chat.id, f"✅ Готово!\n👤 Успешно: {success}\n🚫 Заблокировали: {blocked}\n⚠️ Ошибки: {errors}")

@bot.message_handler(commands=['sell_emoji'])
def sell_item(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ Формат: `/sell_emoji [эмодзи] [цена]`", parse_mode="Markdown")
        return
    
    emoji = parts[1]
    try:
        price = int(parts[2])
    except:
        bot.reply_to(message, "❌ Цена должна быть числом!")
        return
    if price <= 0:
        bot.reply_to(message, "❌ Цена должна быть больше 0!")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM inventory WHERE user_id = ? AND item_type = 'emoji' AND item_value = ? LIMIT 1", (uid, emoji))
    row = cursor.fetchone()
    if not row:
        bot.reply_to(message, "❌ У тебя нет такого эмодзи в инвентаре!")
        return
    cursor.execute("DELETE FROM inventory WHERE id = ?", (row[0],))  # выставляется один экземпляр
    cursor.execute("INSERT INTO auction (seller_id, item_type, item_value, price) VALUES (?, 'emoji', ?, ?)", (uid, emoji, price))
    unequipped = unequip_if_no_copies(cursor, uid, emoji)
    conn.commit()
    note = "\n👕 Эмодзи снят с профиля: у тебя не осталось копий." if unequipped else ""
    bot.reply_to(message, f"✅ Твой лот {emoji} выставлен на аукцион за {price} 💰{note}")

def looks_like_emoji(text):
    """True, если text — ровно ОДИН настоящий эмодзи (цифры, буквы и обычный текст не проходят)."""
    import unicodedata
    text = (text or "").strip()
    if not text or len(text) > 20:
        return False
    count = 0
    prev_zwj = False
    regional = 0
    for ch in text:
        cp = ord(ch)
        if ch == "\u200d":                      # ZWJ — склейка (👨‍👩‍👧)
            prev_zwj = True
            continue
        if ch == "\ufe0f" or 0x1F3FB <= cp <= 0x1F3FF:   # вариант отображения / оттенок кожи
            continue
        if 0x1F1E6 <= cp <= 0x1F1FF:             # флаги: две региональные буквы = один эмодзи
            regional += 1
            if regional % 2 == 1:
                count += 1
            prev_zwj = False
            continue
        if unicodedata.category(ch) in ("So", "Sk") and cp > 0x7F:
            if not prev_zwj:
                count += 1
            prev_zwj = False
            continue
        return False                            # буква, цифра, пробел, знак — не эмодзи
    return count == 1


def resolve_emoji(item):
    """Эмодзи для выдачи: из каталога игры, а если его там нет — любой настоящий эмодзи (🎁).
    Цифры и текст («11», «hello») не проходят — вернётся None."""
    item = (item or "").strip()
    if item in EMOJI_DATA:
        return item
    plain = item.replace("\ufe0f", "")
    for key in EMOJI_DATA:
        if key.replace("\ufe0f", "") == plain:
            return key
    return item if looks_like_emoji(item) else None

@bot.message_handler(commands=['gift'])
def gift_item(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 1):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 1 ур.")
        return
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
        
    parts = message.text.split()
    if len(parts) < 3:
        bot.send_message(message.chat.id, "⚠️ Неверный формат! Пиши так:\n`/gift @юзернейм эмодзи`", parse_mode="Markdown")
        return
        
    target_uname = parts[1].replace("@", "").lower()
    item = resolve_emoji(parts[2])
    if item is None:
        bot.reply_to(message, f"❌ «{parts[2]}» — это не эмодзи.\nПиши так: /gift @юзернейм 🎁")
        return
    
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE LOWER(username) = ?", (target_uname,))
    res = cursor.fetchone()
    
    if res:
        cursor.execute("INSERT INTO inventory (user_id, item_type, item_value) VALUES (?, 'emoji', ?)", (res[0], item))
        conn.commit()
        bot.send_message(message.chat.id, f"🎁 Предмет {item} успешно подарен @{target_uname}!")
        try:
            bot.send_message(res[0], f"🎁 Админ подарил тебе новый предмет: {item}\nПроверь его в инвентаре!")
        except Exception:
            pass
    else:
        bot.send_message(message.chat.id, f"❌ Пользователь @{target_uname} не найден в базе данных бота!")

@bot.message_handler(commands=['give_coins'])
def give_coins_cmd(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 1):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 1 ур.")
        return
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ Формат: /give_coins @username 100")
        return
    username = parts[1].replace("@", "").lower()
    try:
        amount = int(parts[2])
    except:
        bot.reply_to(message, "❌ Сумма должна быть числом!")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Сумма должна быть больше 0!")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    add_coins(res[0], amount)
    bot.reply_to(message, f"✅ Пользователю @{username} выдано {amount} 💰")
    try:
        bot.send_message(res[0], f"🎁 Вам выдали {amount} 💰 монет!")
    except:
        pass

@bot.message_handler(commands=['take_coins'])
def take_coins_cmd(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 2):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 2 ур.")
        return
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ Формат: /take_coins @username 100")
        return
    username = parts[1].replace("@", "").lower()
    try:
        amount = int(parts[2])
    except:
        bot.reply_to(message, "❌ Сумма должна быть числом!")
        return
    if amount <= 0:
        bot.reply_to(message, "❌ Сумма должна быть больше 0!")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, coins FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    if res[1] < amount:
        bot.reply_to(message, f"❌ У @{username} только {res[1]} 💰, нельзя снять {amount}!")
        return
    add_coins(res[0], -amount)
    bot.reply_to(message, f"✅ У @{username} снято {amount} 💰")
    try:
        bot.send_message(res[0], f"⚠️ У вас сняли {amount} 💰 монет.")
    except:
        pass


# Replaced old handler id_ban with Winter command parity implementation.


# Replaced old handler id_unban with Winter command parity implementation.


# Replaced old handler id_freeze with Winter command parity implementation.


# Replaced old handler id_unfreeze with Winter command parity implementation.

@bot.message_handler(commands=['level_up'])
def level_up(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 1):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 1 ур.")
        return
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "⚠️ Использование команды:\n`/level_up @username уровень`\n\nПример: `/level_up @username 100`", parse_mode="Markdown")
        return

    target_username = args[1].replace("@", "").strip().lower()
    try:
        new_lvl = int(args[2])
    except ValueError:
        bot.reply_to(message, "❌ Уровень должен быть целым числом!")
        return

    if new_lvl < 1:
        return bot.reply_to(message, "❌ Уровень должен быть не меньше 1.")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users WHERE username = ?", (target_username,))
    target_row = cursor.fetchone()

    if not target_row:
        bot.reply_to(message, f"❌ Пользователь @{target_username} не найден в базе данных бота.")
        return

    target_id, target_name = target_row
    cursor.execute("UPDATE users SET level = ? WHERE user_id = ?", (new_lvl, target_id))
    conn.commit()

    bot.send_message(
        message.chat.id, 
        f"⭐ *Уровень изменен!*\n\nАдминистратор изменил уровень игроку *{target_name}* (@{target_username}) на *{new_lvl}* 🆙", 
        parse_mode="Markdown"
    )
    try:
        bot.send_message(target_id, f"🆙 Администратор установил твой уровень равным: *{new_lvl}*!", parse_mode="Markdown")
    except:
        pass

@bot.message_handler(commands=['feedback'])
def feedback_command(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message, 
            "⚠️ *Неверный формат!*\nПиши команду и текст отзыва в одном сообщении.\n\n"
            "📝 _Пример:_ `/feedback Нашел баг в игре, бот не засчитал попытку!`", 
            parse_mode="Markdown"
        )
        return

    feedback_text = parts[1].strip()
    username = f"@{message.from_user.username}" if message.from_user.username else "Нет юзернейма"
    first_name = message.from_user.first_name

    admin_report = (
        f"📩 *НОВЫЙ ОТЗЫВ ИГРОКА*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 *Отправитель:* {first_name} ({username})\n"
        f"🆔 *ID пользователя:* `{uid}`\n"
        f"🕒 *Время:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💬 *Текст отзыва:*\n{feedback_text}"
    )

    try:
        bot.send_message(DEVELOPER_CHAT_ID, admin_report, parse_mode="Markdown")
        bot.reply_to(
            message, 
            "✨ *Спасибо за ваш отзыв!*\n"
            "📨 Он успешно доставлен разработчикам проекта. "
            "Мы обязательно его рассмотрим, чтобы сделать Openbot.Ai ещё лучше! 🌐💠",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Ошибка при отправке фидбека админу: {e}")
        bot.reply_to(message, "❌ Произошла ошибка при отправке отзыва. Попробуйте позже.")

@bot.message_handler(commands=['bio'])
def set_bio_command(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Используй так: `/bio твой текст о себе`", parse_mode='Markdown')
        return
    
    new_bio = parts[1].strip()
    if len(new_bio) > 200:
        bot.reply_to(message, "❌ Слишком длинное био, максимум 200 символов")
        return
    
    try:
        openbot.update_bio(uid, new_bio)
    except Exception:
        logging.getLogger('master_bot.bio').exception('Openbot API bio update failed')
        return bot.reply_to(message, '⚠️ Не удалось обновить описание: Openbot ID временно недоступен.')
    bot.reply_to(message, '✅ Описание профиля обновлено!')

@bot.message_handler(commands=['id_profile'])
def id_profile(message):
    uid, cid = message.from_user.id, message.chat.id
    global_data = openbot.get_id(uid)
    reason = check_ban(uid)

    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return

    if global_data:
        g_name = global_data[2]
        g_tag = global_data[5] or "Не установлен"
        g_bio = global_data[8] or "Не установлена"
        g_data = global_data[7]
        
        bots_list = openbot.get_active_bots(uid)
        g_active_bot = ", ".join(bots_list) if bots_list else "Ни в каких"
        g_status = ROLE.get(global_data[6], "👤 Игрок")
            
        text = f"""**🌐 Общий профиль Openbot AI ID**

**🏷 Имя:** `{g_name}`
**🆔 Тег:** `{g_tag}`
**🎭 Статус:** `{g_status}`
**🤖 Боты:** `{g_active_bot}`
**🗄 Создан:** `{g_data}`
**📝 О себе:** `{g_bio}`"""
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton('⬅️ Назад в профиль', callback_data='profile')
        )
        bot.send_message(cid, text, reply_markup=markup, parse_mode="Markdown")
    else:
        text = "❌ У вас еще не создан Openbot AI ID. Напишите /start."
        bot.send_message(cid, text)

def get_profile_text(user_id):
    user = get_user(user_id)
    global_data = openbot.get_id(user_id)

    if global_data:
        g_name = global_data[2]
        equipped = get_equipped_emoji(user_id) if user else ""
        g_tag = global_data[5] or "Не установлен"

        full_name = f"{equipped} {g_name}".strip()
    else:
        full_name = user[2] if user else "Игрок"
        g_tag = "Отсутствует"

    display_status = STATUS.get(
        user[3] if user else "👤 Игрок"
    )

    title = PROFILE_TITLES.get(
        user[4] if user else "none",
        "Без титула"
    )

    if not user:
        return "❌ Профиль не найден."

    profile_text = f"""<b>ПРОФИЛЬ</b>
━━━━━━━━━━━━━━━━━━
👤 <b>Имя в сети:</b> {full_name}
🆔 <b>Ваш Тег:</b> <code>{g_tag}</code>
🎭 <b>Статус:</b> {display_status}
🎖 <b>Игровой титул:</b> {title}

📊 <b>ИГРОВАЯ СТАТИСТИКА:</b>
⭐ <b>Уровень:</b> {user[7]}
✨ <b>Опыт:</b> {user[8]}
💰 <b>Монеты:</b> {user[5]} 💰
☃️ <b>Снежки:</b> {get_snowballs(user_id)}
━━━━━━━━━━━━━━━━━━
"""

    return profile_text

@bot.message_handler(commands=['profile'])
def profile_command(message):
    user_id = message.from_user.id

    # Здесь твой существующий код профиля
    # Например:
    user = get_user(user_id)
    global_data = openbot.get_id(user_id)

    if not user:
        bot.send_message(
            message.chat.id,
            "❌ Профиль не найден."
        )
        return

    # Если у тебя уже есть готовая функция формирования профиля,
    # используй её здесь:
    profile_text = get_profile_text(user_id)

    bot.send_message(
        message.chat.id,
        profile_text,
        parse_mode="HTML"
    )


# Replaced old handler set_status_command with Winter command parity implementation.

@bot.message_handler(commands=['refund'])
def handle_refund(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if openbot.is_globally_banned(uid):
        bot.reply_to(message, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    
    if message.chat.type != 'private' or not openbot.check_access(uid, 'owner'):
        return bot.reply_to(message, '⛔ Возврат Stars доступен только владельцу в ЛС.')
    
    if not openbot.check_access(message.from_user.id, ("developer", "tester", "admin", "owner"), 2):
        bot.reply_to(message, "⛔ Нужен статус developer/tester/admin от 2 ур.")
        return
    
    # 1. Проверяем, что команду отправил именно админ
    # 2. Разбиваем сообщение, чтобы достать ID транзакции
    # Ожидаемый формат в чате: /refund 123456789ABCDEF...
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        bot.reply_to(message, "❌ Ошибка! Введите команду в формате:\n`/refund ИД_ТРАНЗАКЦИИ`", parse_mode="Markdown")
        return
        
    charge_id = args[1].strip()
    
    try:
        # 3. Вызываем официальный метод Telegram для возврата звезд.
        # user_id передаем того, КТО вызвал команду (для теста — вы сами).
        # Метод автоматически найдет плательщика по charge_id.
        bot.refund_star_payment(
            user_id=message.from_user.id, 
            telegram_payment_charge_id=charge_id
        )
        bot.reply_to(message, f"✅ Возврат Stars по транзакции `{charge_id}` возвращены обратно на ваш личный баланс.", parse_mode="Markdown")
        
    except Exception as e:
        # Ошибка будет, если вы опечатались в ID или с момента теста прошло больше 21 дня
        bot.reply_to(message, f"❌ Не удалось сделать возврат.\nПроверьте ID транзакции или холд (21 день).\n\nОшибка API: `{e}`", parse_mode="Markdown")

# =========== Обновленный Обработчик кнопок =============
from datetime import timedelta, timezone

def _reply_staff(user_id):
    try:
        user = openbot.get_user(user_id)
        return (isinstance(user, dict) and user.get('exists') and
                str(user.get('status', '')).lower() in {'owner', 'developer'})
    except Exception:
        return False


def _reply_regular_user(user_id):
    # Не считаем сотрудника обычным пользователем при сбое Openbot API.
    try:
        info = openbot.get_user(user_id)
        return (isinstance(info, dict) and info.get('exists') and
                str(info.get('status', '')).lower() == 'user')
    except Exception:
        return False


def _reply_off_enabled():
    with sqlite3.connect(DB_NAME, timeout=10) as db:
        row = db.execute("SELECT value FROM system_settings WHERE key='reply_emergency_off'").fetchone()
    return bool(row and row[0] == '1')


def _reply_prepare_db():
    with sqlite3.connect(DB_NAME, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS reply_restore ('
                   'user_id INTEGER PRIMARY KEY, kb_type TEXT NOT NULL)')
        # Не теряем информацию о том, кто уже нажал кнопку перехода.
        db.execute('CREATE TABLE IF NOT EXISTS reply_inline_ack ('
                   'user_id INTEGER PRIMARY KEY)')


def _reply_needs_switch(user_id):
    """До /reply_off пользователь был в Reply и ещё не открыл Inline."""
    with sqlite3.connect(DB_NAME, timeout=10) as db:
        row = db.execute('SELECT kb_type FROM reply_restore WHERE user_id=?',
                         (user_id,)).fetchone()
        acknowledged = db.execute('SELECT 1 FROM reply_inline_ack WHERE user_id=?',
                                  (user_id,)).fetchone()
    return bool(row and row[0] == 'reply' and not acknowledged)


_reply_prepare_db()


def mark_reply_mode(user_id):
    if _reply_off_enabled() and _reply_regular_user(user_id):
        return
    if get_user_settings(user_id)[0] != 'reply':
        with sqlite3.connect(DB_NAME, timeout=10) as db:
            db.execute("UPDATE users SET kb_type='reply' WHERE user_id=?", (user_id,))


_REPLY_EMERGENCY_MESSAGE = (
    "🚨 <b>ВРЕМЕННОЕ ОГРАНИЧЕНИЕ REPLY</b>\n\n"
    "В связи с техническими работами Reply-клавиатура временно отключена "
    "для обычных игроков.\n\n"
    "Продолжай пользоваться ботом через Inline-кнопки под сообщениями.\n\n"
    "С уважением, команда Openbot.Ai 🌐💠"
)


def _reply_emergency_markup(user_id):
    kb = types.InlineKeyboardMarkup()
    if _reply_needs_switch(user_id):
        kb.add(types.InlineKeyboardButton('➡️ Перейти на Inline',
                                          callback_data='reply:to_inline'))
    else:
        kb.add(types.InlineKeyboardButton('🏠 Главное меню', callback_data='back'))
    return kb


def _reply_reject_message(message):
    if not (_reply_off_enabled() and _reply_regular_user(message.from_user.id)):
        return False
    bot.send_message(message.chat.id, _REPLY_EMERGENCY_MESSAGE,
                     reply_markup=_reply_emergency_markup(message.from_user.id),
                     parse_mode='HTML')
    return True


def _reply_old_keyboard_button(text):
    value = (text or '').strip()
    exact = {
        '🎄 MASTER BOT', '❄️ Зимние игры', '👤 Профиль', '🎁 Зимняя лавка',
        '🎒 Инвентарь', '🏪 Рынок', '⚙️ Настройки', '⚙ Настройки',
        '⬅ Главное меню', '⬅ В главное меню', '⬅ Игры', '⬅ Магазин',
        '⬅ Зимняя лавка', '⬅ Инвентарь', '⬅ Рынок',
        '🎁 Кейсы', '🏆 Титулы', '⭐ Магазин Stars', '🏆 Мои титулы',
        '👑 Титулы за монеты', '❄️ Титулы за снежинки', '☃️ Обмен снежков',
        '📊 Лидеры', '📅 Календарь декабря', '📅 Календарь ноября',
        '🔮 Тайна снежного шара', '❄️ Снежинки vs ⛄ Снеговики', '⛄ Битва со снежным големом',
        '🎁 Подарок или сюрприз', '🔎 Поиск зимних сокровищ', '❌⭕ Крестики-нолики',
        '🛑 Выйти из игры', '⬅ Предыдущие кейсы', 'Следующие кейсы ➡',
        '🧊 Лабиринт', '🎄 Гирлянды', '🐧 Экспедиция', '🔔 Морозный шифр',
        '📦 Мои лоты', '◀️ Предыдущая страница', 'Следующая страница ▶️',
        '◀️ Предыдущая страница рынка', 'Следующая страница рынка ▶️',
    }
    prefixes = ('⌨ Переключить вид кнопок:', '🔔 Уведомления:',
                '🔕 Уведомления:', '👁 ID для других:', '🙈 ID для других:',
                '🛒 ', '🔓 Открыть ', '👕 ', '💰 Продать ', '🏪 Выставить ',
                '❌ Снять лот #', '❄️ Сугроб ', '🚪 Комната ',
                '🧪 Шар ', '🎁 Подарок ', '❌⭕ ', '🏆 ', '🔒 ')
    return value in exact or value.startswith(prefixes)


@bot.message_handler(func=lambda m: _reply_old_keyboard_button(m.text)
                     and _reply_off_enabled() and _reply_regular_user(m.from_user.id))
def _reply_off_regular_text(message):
    """Старые Reply-кнопки не обходят режим ограничения; есть путь на Inline."""
    _reply_reject_message(message)


@bot.callback_query_handler(
    func=lambda c: (c.data or '') == 'toggle_kb'
    and _reply_off_enabled()
    and _reply_regular_user(c.from_user.id)
)
def reply_disabled_inline_settings(call):
    """В Inline заменяем настройки полным экраном ограничения, не popup."""
    uid = call.from_user.id
    bot.answer_callback_query(call.id)  # только убираем индикатор загрузки
    keyboard = _reply_emergency_markup(uid)
    try:
        bot.edit_message_text(
            _REPLY_EMERGENCY_MESSAGE,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


@bot.callback_query_handler(func=lambda c: (c.data or '') == 'reply:to_inline')
def _reply_switch_inline(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    needs_switch = _reply_needs_switch(uid)

    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("UPDATE users SET kb_type='inline' WHERE user_id=?", (uid,))
        db.execute('INSERT OR IGNORE INTO reply_inline_ack(user_id) VALUES(?)', (uid,))

    bot.answer_callback_query(call.id, '✅ Главное меню Inline')
    if needs_switch:
        # Telegram убирает старые Reply-кнопки только через отдельное сообщение.
        bot.send_message(chat_id, '⌨️ Reply-клавиатура скрыта.',
                         reply_markup=types.ReplyKeyboardRemove())
    try:
        bot.edit_message_text(
            '🏠 <b>Главное меню Master Bot</b>',
            chat_id=chat_id, message_id=message_id,
            reply_markup=main_mune(), parse_mode='HTML',
        )
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


@bot.message_handler(commands=['reply_off'])
def reply_off_everyone(message):
    """Временно запрещает Reply только обычным игрокам, сохраняя их выбор."""
    if message.chat.type != 'private' or not _reply_staff(message.from_user.id):
        return bot.reply_to(message, '⛔ Команда доступна только developer и owner.')
    if _reply_off_enabled():
        return bot.reply_to(message, 'ℹ️ Reply уже ограничена. Для восстановления: /reply_on.')
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        people = db.execute('SELECT user_id, kb_type FROM users WHERE user_id IS NOT NULL').fetchall()
    switched = []
    for user_id, old_mode in people:
        if not _reply_regular_user(user_id):
            continue
        with sqlite3.connect(DB_NAME, timeout=15) as db:
            db.execute('INSERT OR IGNORE INTO reply_restore(user_id,kb_type) VALUES(?,?)',
                       (user_id, old_mode if old_mode in ('inline','reply') else 'inline'))
            db.execute("UPDATE users SET kb_type='inline' WHERE user_id=?", (user_id,))
            db.execute('DELETE FROM reply_inline_ack WHERE user_id=?', (user_id,))
        if old_mode == 'reply':
            switched.append(user_id)
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("INSERT INTO system_settings(key,value) VALUES('reply_emergency_off','1') "
                   "ON CONFLICT(key) DO UPDATE SET value='1'")
    notified = 0
    for user_id in switched:
        try:
            bot.send_message(user_id, _REPLY_EMERGENCY_MESSAGE,
                             reply_markup=_reply_emergency_markup(user_id),
                             parse_mode='HTML')
            notified += 1
        except Exception:
            logging.getLogger('master_bot.reply').warning(
                'Не удалось уведомить Reply-пользователя %s', user_id)
    bot.reply_to(message, f'✅ Reply отключена для обычных игроков. '
                          f'Режимы сохранены. Было в Reply: {len(switched)}. '
                          f'Уведомлено: {notified}. Inline-пользователей не переключали.')


@bot.message_handler(commands=['reply_on'])
def reply_on_everyone(message):
    """Возвращает старые предпочтения, без лишних сообщений Inline-пользователям."""
    if message.chat.type != 'private' or not _reply_staff(message.from_user.id):
        return bot.reply_to(message, '⛔ Команда доступна только developer и owner.')
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        saved = db.execute('SELECT user_id,kb_type FROM reply_restore').fetchall()
    restored_reply = []
    restored = 0
    for uid, mode in saved:
        if not _reply_regular_user(uid):
            continue
        with sqlite3.connect(DB_NAME, timeout=15) as db:
            db.execute('UPDATE users SET kb_type=? WHERE user_id=?', (mode, uid))
            db.execute('DELETE FROM reply_restore WHERE user_id=?', (uid,))
            db.execute('DELETE FROM reply_inline_ack WHERE user_id=?', (uid,))
        restored += 1
        if mode == 'reply':
            restored_reply.append(uid)
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute("INSERT INTO system_settings(key,value) VALUES('reply_emergency_off','0') "
                   "ON CONFLICT(key) DO UPDATE SET value='0'")
    notified = 0
    for uid in restored_reply:
        try:
            bot.send_message(uid, '✅ Ограничение Reply снято. Твой прежний режим восстановлен.',
                             reply_markup=main_mune_reply())
            notified += 1
        except Exception:
            logging.getLogger('master_bot.reply').warning(
                'Не удалось восстановить клавиатуру %s', uid)
    bot.reply_to(message, f'✅ Reply снова доступна. Настройки восстановлены у {restored} игроков. '
                          f'Уведомлено пользователей Reply: {notified}. '
                          'Пользователям Inline лишних сообщений не отправляли.')



# ============ MASTER BOT: НАKАЗАНИЯ ============
"""Independent Master Bot punishment registry; does not alter Openbot ID roles."""
import calendar
import re

from datetime import datetime, timedelta, timezone


_punishment_DB_PATH = Path(__file__).resolve().parent / DB_NAME
_punishment_TIME_RE = re.compile(r"^(\d+)(h|d|mo|y)$", re.I)


def _punishment_init():
    with sqlite3.connect(_punishment_DB_PATH, timeout=15) as db:
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("""CREATE TABLE IF NOT EXISTS punishments (
            user_id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
            reason TEXT NOT NULL, issued_by INTEGER NOT NULL,
            issued_at TEXT NOT NULL, expires_at TEXT,
            username TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS punishment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, kind TEXT NOT NULL,
            reason TEXT NOT NULL, actor_id INTEGER NOT NULL,
            action TEXT NOT NULL, created_at TEXT NOT NULL)""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(punishments)")}
        if "previous_status" not in columns:
            db.execute("ALTER TABLE punishments ADD COLUMN previous_status TEXT")
        # Миграция действующих банов из предыдущей версии: ранее их добавляли
        # в punishments, но забывали обновить users.status.
        current_time = datetime.now(timezone.utc).isoformat()
        db.execute(
            """UPDATE punishments SET previous_status = COALESCE(
                   (SELECT NULLIF(status, 'banned') FROM users
                    WHERE users.user_id = punishments.user_id), 'user')
               WHERE previous_status IS NULL
                 AND (expires_at IS NULL OR expires_at > ?)""",
            (current_time,),
        )
        db.execute(
            """UPDATE users SET status='banned', ban_reason=(
                   SELECT p.reason FROM punishments AS p
                   WHERE p.user_id=users.user_id)
               WHERE EXISTS (
                   SELECT 1 FROM punishments AS p
                   WHERE p.user_id=users.user_id
                     AND (p.expires_at IS NULL OR p.expires_at > ?))""",
            (current_time,),
        )


def _punishment__add_months(dt, count):
    month_index = dt.year * 12 + dt.month - 1 + count
    year, month_index = divmod(month_index, 12)
    if year > 9999:
        raise ValueError("Слишком большой срок")
    month = month_index + 1
    return dt.replace(year=year, month=month,
                      day=min(dt.day, calendar.monthrange(year, month)[1]))


def _punishment_expires_from(spec):
    match = _punishment_TIME_RE.fullmatch(spec.lower())
    if not match:
        raise ValueError("Время: 12h, 7d, 3mo или 1y (999y = навсегда)")
    count, unit = int(match[1]), match[2]
    if count < 1:
        raise ValueError("Срок должен быть положительным")
    if spec.lower() == "999y":
        return None
    now = datetime.now(timezone.utc)
    if unit == "h":
        result = now + timedelta(hours=count)
    elif unit == "d":
        result = now + timedelta(days=count)
    elif unit == "mo":
        result = _punishment__add_months(now, count)
    else:
        result = _punishment__add_months(now, 12 * count)
    return result.isoformat()


def _punishment_restore_status(user_id, previous_status=None):
    """После LOCAL UNBAN не снимает GLOBAL BAN и сохраняет настоящую роль."""
    fallback = (
        previous_status if previous_status and previous_status != 'banned'
        else 'user'
    )
    info = get_openbot_user(user_id)
    if info:
        fallback = str(info.get('status') or fallback).lower()

    # Если сервер недоступен, не снимаем сохранённый маркер глобального бана.
    global_ban = None
    try:
        global_ban = openbot.get_global_ban(user_id)
    except Exception:
        pass

    with sqlite3.connect(_punishment_DB_PATH, timeout=15) as db:
        db.execute('PRAGMA busy_timeout=15000')
        db.row_factory = sqlite3.Row
        local = db.execute(
            'SELECT kind, reason FROM punishments WHERE user_id=?',
            (user_id,),
        ).fetchone()
        saved = db.execute(
            'SELECT global_ban_kind, ban_reason FROM users WHERE user_id=?',
            (user_id,),
        ).fetchone()
        if not saved:
            return
        if global_ban is None:
            kind = saved['global_ban_kind']
            global_reason = saved['ban_reason']
        else:
            kind = global_ban.get('kind') if global_ban.get('banned') else None
            global_reason = global_ban.get('reason')
        status = 'banned' if local or kind else fallback
        reason = local['reason'] if local else global_reason if kind else None
        db.execute(
            'UPDATE users SET status=?, ban_reason=?, global_ban_kind=? '
            'WHERE user_id=?', (status, reason, kind, user_id),
        )


def _punishment_notify_unban(user_id, kind, *, expired=False):
    """Не обещает полный доступ, если у игрока остался другой бан."""
    title = 'HARD BAN' if kind == 'hard_ban' else 'BAN'
    reason = (
        'Срок локальной блокировки истёк.' if expired
        else 'Администрация сняла локальную блокировку.'
    )
    row = conn.execute(
        'SELECT status FROM users WHERE user_id=?', (user_id,),
    ).fetchone()
    access = (
        '⚠️ Другие ограничения всё ещё действуют. Проверь /ban_info.'
        if row and row[0] == 'banned'
        else '🎮 Доступ к Master Bot восстановлен.'
    )
    try:
        bot.send_message(user_id, f'✅ {title} снят!\n{reason}\n{access}')
        return True
    except Exception:
        logging.getLogger('master_bot.punishments').warning(
            'Не удалось уведомить о снятии бана user_id=%s', user_id,
        )
        return False


def _punishment_get(user_id):
    expired = None
    with sqlite3.connect(_punishment_DB_PATH, timeout=15) as db:
        db.execute("PRAGMA busy_timeout=15000")
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM punishments WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        entry = dict(row)
        if entry["expires_at"] and datetime.fromisoformat(entry["expires_at"]) <= datetime.now(timezone.utc):
            db.execute("DELETE FROM punishments WHERE user_id=?", (user_id,))
            db.execute(
                """INSERT INTO punishment_history
                   (user_id,kind,reason,actor_id,action,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (user_id, entry["kind"], entry["reason"], 0,
                 "expired", datetime.now(timezone.utc).isoformat()),
            )
            expired = entry
        else:
            return entry

    if expired:
        _punishment_restore_status(user_id, expired.get("previous_status"))
        _punishment_notify_unban(user_id, expired["kind"], expired=True)
    return None


def _punishment_set_ban(user_id, username, kind, reason, actor_id, duration):
    if kind not in {"ban", "hard_ban"} or not reason.strip():
        raise ValueError("Неверное наказание или пустая причина")
    expiry = _punishment_expires_from(duration)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_punishment_DB_PATH, timeout=15) as db:
        db.execute("PRAGMA busy_timeout=15000")
        previous_row = db.execute(
            "SELECT previous_status FROM punishments WHERE user_id=?", (user_id,)
        ).fetchone()
        current_row = db.execute(
            "SELECT status FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not current_row:
            raise ValueError("Игрок отсутствует в базе Master Bot")
        previous = (
            previous_row[0] if previous_row and previous_row[0]
            else (current_row[0] if current_row[0] != "banned" else "user")
        )
        db.execute(
            """INSERT INTO punishments
               (user_id,kind,reason,issued_by,issued_at,expires_at,username,previous_status)
               VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
               kind=excluded.kind, reason=excluded.reason, issued_by=excluded.issued_by,
               issued_at=excluded.issued_at, expires_at=excluded.expires_at,
               username=excluded.username, previous_status=excluded.previous_status""",
            (user_id, kind, reason.strip(), actor_id, now, expiry, username, previous),
        )
        # Локальный статус. Глобальную роль Openbot ID не меняем.
        db.execute(
            "UPDATE users SET status='banned', ban_reason=? WHERE user_id=?",
            (reason.strip(), user_id),
        )
        db.execute(
            """INSERT INTO punishment_history
               (user_id,kind,reason,actor_id,action,created_at)
               VALUES(?,?,?,?,?,?)""",
            (user_id, kind, reason.strip(), actor_id, "issued", now),
        )
    return expiry


def _punishment_lift(user_id, kind, actor_id):
    with sqlite3.connect(_punishment_DB_PATH, timeout=15) as db:
        db.execute("PRAGMA busy_timeout=15000")
        row = db.execute(
            "SELECT reason,previous_status FROM punishments WHERE user_id=? AND kind=?",
            (user_id, kind),
        ).fetchone()
        if not row:
            return False
        db.execute("DELETE FROM punishments WHERE user_id=? AND kind=?", (user_id, kind))
        db.execute(
            """INSERT INTO punishment_history
               (user_id,kind,reason,actor_id,action,created_at)
               VALUES(?,?,?,?,?,?)""",
            (user_id, kind, row[0], actor_id, "lifted", datetime.now(timezone.utc).isoformat()),
        )
        previous_status = row[1]
    _punishment_restore_status(user_id, previous_status)
    return True

_punishment_init()



def _punish_admin(message):
    try:
        return (message.chat.type == 'private' and
                openbot.check_access(message.from_user.id,
                                     ('developer', 'tester', 'admin', 'owner'), 1))
    except Exception:
        return False


def _target_username(raw):
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", raw):
        raise ValueError('Укажи @username (от 5 до 32 символов)')
    username = raw[1:].lower()
    row = conn.execute('SELECT user_id FROM users WHERE LOWER(username)=?', (username,)).fetchone()
    if not row:
        raise ValueError('Пользователь не найден в базе Master Bot')
    return int(row[0]), username


# ============ Каждая команда наказания — отдельный обработчик ============
# Вспомогательные функции выше только работают с БД и проверяют username.

@bot.message_handler(commands=['ban'])
def ban_user(message):
    """Обычный BAN: блокировка игр, покупок и получения наград."""
    if not _punish_admin(message):
        bot.reply_to(message, '⛔ Команда доступна администрации в ЛС бота.')
        return

    parts = (message.text or '').split(maxsplit=3)
    if len(parts) != 4:
        bot.reply_to(message, 'Формат: /ban @username 7d Причина\nСроки: h, d, mo, y, 999y')
        return

    try:
        user_id, username = _target_username(parts[1])
        if user_id == message.from_user.id:
            raise ValueError('Нельзя заблокировать самого себя')

        info = openbot.get_user(user_id)
        if not isinstance(info, dict):
            raise RuntimeError('Не удалось проверить глобальную роль пользователя')
        if info.get('status') in ('owner', 'developer', 'admin', 'tester'):
            raise ValueError('Служебные аккаунты защищены от бана')

        reason = parts[3].strip()
        if not reason:
            raise ValueError('Причина обязательна')
        expiry = _punishment_set_ban(user_id, username, 'ban', reason,
                                     message.from_user.id, parts[2])
        details = _punishment_get(user_id)
        if details:
            _global_notify(
                user_id, _local_format_ban(
                    details, admin_username=message.from_user.username
                )
            )
        bot.reply_to(message, f'✅ Игрок @{username} забанен.')
    except (ValueError, OverflowError) as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /ban')
        bot.reply_to(message, '❌ Не удалось выдать BAN. Проверь /logs.')


@bot.message_handler(commands=['unban'])
def unban_by_username(message):
    """Снять только обычный BAN."""
    if not _punish_admin(message):
        bot.reply_to(message, '⛔ Команда доступна администрации в ЛС бота.')
        return

    parts = (message.text or '').split()
    if len(parts) != 2:
        bot.reply_to(message, 'Формат: /unban @username')
        return

    try:
        user_id, username = _target_username(parts[1])
        if not _punishment_lift(user_id, 'ban', message.from_user.id):
            raise ValueError('У пользователя нет активного обычного BAN')
        delivered = _punishment_notify_unban(user_id, 'ban')
        bot.reply_to(message, f'✅ Обычный BAN снят с @{username}' +
                     ('' if delivered else '\n⚠️ Не удалось доставить уведомление игроку.'))
    except ValueError as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /unban')
        bot.reply_to(message, '❌ Не удалось снять BAN. Проверь /logs.')


@bot.message_handler(commands=['hard_ban'])
def hard_ban_user(message):
    """HARD BAN: полностью закрывает доступ к Master Bot."""
    if not _punish_admin(message):
        bot.reply_to(message, '⛔ Команда доступна администрации в ЛС бота.')
        return

    parts = (message.text or '').split(maxsplit=3)
    if len(parts) != 4:
        bot.reply_to(message, 'Формат: /hard_ban @username 1mo Причина\nСроки: h, d, mo, y, 999y')
        return

    try:
        user_id, username = _target_username(parts[1])
        if user_id == message.from_user.id:
            raise ValueError('Нельзя заблокировать самого себя')

        info = openbot.get_user(user_id)
        if not isinstance(info, dict):
            raise RuntimeError('Не удалось проверить глобальную роль пользователя')
        if info.get('status') in ('owner', 'developer', 'admin', 'tester'):
            raise ValueError('Служебные аккаунты защищены от бана')

        reason = parts[3].strip()
        if not reason:
            raise ValueError('Причина обязательна')
        expiry = _punishment_set_ban(user_id, username, 'hard_ban', reason,
                                     message.from_user.id, parts[2])
        details = _punishment_get(user_id)
        if details:
            _global_notify(
                user_id, _local_format_ban(
                    details, admin_username=message.from_user.username
                )
            )
        bot.reply_to(message, f'✅ Игрок @{username} забанен.')
    except (ValueError, OverflowError) as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /hard_ban')
        bot.reply_to(message, '❌ Не удалось выдать HARD BAN. Проверь /logs.')


@bot.message_handler(commands=['unhard_ban'])
def unhard_ban_user(message):
    """Снять только HARD BAN."""
    if not _punish_admin(message):
        bot.reply_to(message, '⛔ Команда доступна администрации в ЛС бота.')
        return

    parts = (message.text or '').split()
    if len(parts) != 2:
        bot.reply_to(message, 'Формат: /unhard_ban @username')
        return

    try:
        user_id, username = _target_username(parts[1])
        if not _punishment_lift(user_id, 'hard_ban', message.from_user.id):
            raise ValueError('У пользователя нет активного HARD BAN')
        delivered = _punishment_notify_unban(user_id, 'hard_ban')
        bot.reply_to(message, f'✅ HARD BAN снят с @{username}' +
                     ('' if delivered else '\n⚠️ Не удалось доставить уведомление игроку.'))
    except ValueError as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /unhard_ban')
        bot.reply_to(message, '❌ Не удалось снять HARD BAN. Проверь /logs.')


@bot.message_handler(commands=['ban_id'])
def ban_id_user(message):
    """Резерв для ЧС проекта. Не выдаёт HARD BAN под другим названием."""
    bot.reply_to(message,
                 "ℹ️ /ban_id пока зарезервирована для ЧС проекта.\n"
                 "Обычный бан: /ban @username 7d Причина\n"
                 "Строгий бан: /hard_ban @username 7d Причина")


@bot.message_handler(commands=['unban_id'])
def unban_id_user(message):
    """Резерв для разблокировки ЧС проекта."""
    bot.reply_to(message, "ℹ️ /unban_id пока не используется. Для HARD BAN: /unhard_ban @username")


# ============ ГЛОБАЛЬНЫЙ СТАТУС OPENBOT ID ============
@bot.message_handler(commands=['get_status'])
def get_status_command(message):
    """/get_status @username [developer|tester|admin|coder|user] [1-4].

    Без роли показывает текущий статус. С ролью и уровнем отправляет
    изменение через защищённый административный маршрут FastAPI.
    """
    if message.chat.type != 'private':
        bot.reply_to(message, '⛔ Команда доступна только в ЛС бота.')
        return

    try:
        actor = openbot.get_user(message.from_user.id)
        if not isinstance(actor, dict) or not actor.get('exists'):
            raise PermissionError('Не удалось подтвердить твои полномочия через Openbot ID')
        actor_role = str(actor.get('status', 'user')).lower()
        actor_level = int(actor.get('level', 0))
        if not (actor_role == 'owner' or (actor_role == 'developer' and actor_level >= 4)):
            raise PermissionError('Изменять глобальные роли могут только owner и developer IV')

        args = (message.text or '').split()
        if len(args) not in (2, 4):
            raise ValueError('Формат: /get_status @username [статус уровень]\n'
                             'Пример: /get_status @username tester 2')

        user_id, username = _target_username(args[1])
        target = openbot.get_user(user_id)
        if not isinstance(target, dict) or not target.get('exists'):
            raise ValueError('Глобальный Openbot ID пользователя не найден')

        previous_role = str(target.get('status', 'user')).lower()
        previous_level = int(target.get('level', 1))

        if len(args) == 2:
            bot.reply_to(message, f'🌐 @{username}\n🎭 Роль: {ROLE.get(previous_role, previous_role)}'
                                  f'\n⭐ Уровень: {previous_level}')
            return

        role = args[2].strip().lower()
        allowed = {'user', 'coder', 'tester', 'admin', 'developer'}
        if role not in allowed:
            raise ValueError('Разрешённые статусы: user, coder, tester, admin, developer')
        try:
            level = int(args[3])
        except ValueError as exc:
            raise ValueError('Уровень должен быть числом от 1 до 4') from exc
        if level < 1 or level > 4:
            raise ValueError('Уровень Openbot ID должен быть от 1 до 4')
        if role == 'user' and level != 1:
            raise ValueError('Для статуса user используй уровень 1')
        if user_id == message.from_user.id:
            raise PermissionError('Нельзя изменять собственный глобальный статус этой командой')
        if previous_role == 'owner':
            raise PermissionError('Статус владельца нельзя изменять через Master Bot')
        if actor_role != 'owner' and previous_role == 'developer' and previous_level >= 4:
            raise PermissionError('Разработчика IV уровня может изменить только владелец')

        admin_key = os.environ.get('OPENBOT_ADMIN_API_KEY', '').strip()
        if not admin_key:
            raise RuntimeError('Для изменения роли не настроен OPENBOT_ADMIN_API_KEY')
        api_url = os.environ.get('OPENBOT_API_URL', 'http://127.0.0.1:8000').rstrip('/')
        location = urlsplit(api_url)
        if location.scheme not in {'http', 'https'} or not location.hostname:
            raise ValueError('Некорректный OPENBOT_API_URL')
        if location.scheme == 'http' and location.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('Для удалённого Openbot API требуется HTTPS')
        body = json.dumps({'user_id': user_id, 'status': role, 'level': level}).encode('utf-8')
        request = Request(
            api_url + '/api/legacy/account/status',
            data=body,
            headers={'X-Admin-Key': admin_key, 'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urlopen(request, timeout=10) as response:
                answer = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise RuntimeError(f'FastAPI вернул HTTP {exc.code} (проверь права ключа и маршрут)') from exc
        except URLError as exc:
            raise RuntimeError('Не удалось подключиться к Openbot API') from exc

        if answer.get('ok') is not True:
            raise RuntimeError('FastAPI не подтвердил изменение статуса')

        # Обновление локальной копии статуса сделает существующий sync_global_status.
        sync_global_status(user_id)
        logging.getLogger('master_bot.global_status').info(
            'actor=%s target=%s role=%s level=%s',
            message.from_user.id, user_id, role, level,
        )
        bot.reply_to(message, f'✅ Глобальный статус изменён!\n👤 @{username}\n'
                              f'🎭 {ROLE.get(role, role)}\n⭐ Уровень: {level}')
    except (PermissionError, ValueError) as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception as exc:
        error_logger.exception('Ошибка /get_status')
        # Не раскрывать ошибочные ответы сервера и секреты в Telegram.
        bot.reply_to(message, f'❌ Не удалось изменить или получить статус. {exc}')


# ============ 🌐 OPENBOT ID: ГЛОБАЛЬНЫЕ НАКАЗАНИЯ ============
# Команды расположены прямо в основном файле Master Bot.
# Сервер FastAPI хранит наказания в центральной БД.
# Только staff API-ключ, принадлежащий инициатору, может выдать наказание.

from openbot_api import APIError


def _global_target(raw):
    """@username доступен, только если пользователь уже известен Master Bot."""
    normalized = "@" + raw.strip().lstrip("@")
    return _target_username(normalized)


def _global_command_args(message, *, lifting=False):
    if message.chat.type != "private":
        raise ValueError("Глобальные наказания доступны только в ЛС бота")
    args = (message.text or "").split(maxsplit=3)
    if lifting:
        if len(args) != 2:
            raise ValueError("Формат: /global_unban @username")
        uid, username = _global_target(args[1])
        return uid, username
    if len(args) != 4 or not args[3].strip():
        raise ValueError("Формат: /global_ban @username 7d Причина")
    uid, username = _global_target(args[1])
    if uid == message.from_user.id:
        raise ValueError("Нельзя заблокировать самого себя")
    return uid, username, args[2], args[3].strip()


def _global_notify(uid, text):
    try:
        bot.send_message(uid, text)
        return True
    except Exception:
        return False


def _ban_date_time(raw):
    """Переводит сохранённую ISO-дату (UTC) в местное время сервера."""
    if not raw:
        return 'Неизвестно', 'Неизвестно'
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        return local.strftime('%d.%m.%Y'), local.strftime('%H:%M')
    except (ValueError, TypeError, OverflowError):
        return 'Неизвестно', 'Неизвестно'


def _ban_admin_username(ban, username=None):
    """Возвращает @username модератора или его ID, если username недоступен."""
    if username:
        return '@' + str(username).strip().lstrip('@')

    actor_id = ban.get('actor_id') or ban.get('issued_by')
    if not actor_id:
        return 'Неизвестен'

    try:
        row = conn.execute(
            'SELECT username FROM users WHERE user_id=?', (actor_id,)
        ).fetchone()
        if row and row[0]:
            return '@' + str(row[0]).lstrip('@')
    except sqlite3.Error:
        pass

    # Для старых записей берём имя через Telegram, если его нет в локальной БД.
    try:
        chat = bot.get_chat(int(actor_id))
        if getattr(chat, 'username', None):
            return '@' + chat.username
    except Exception:
        pass
    return f'ID {actor_id}'


def _ban_notice(ban, *, global_scope, admin_username=None):
    """Same format for local and global Master Bot punishments."""
    names = {'ban': 'BAN', 'hard_ban': 'HARD BAN',
             'global_ban': 'GLOBAL BAN', 'global_hard_ban': 'GLOBAL HARD BAN'}
    kind = names.get(ban.get('kind'), 'BAN')
    began = ban.get('created_at') if global_scope else ban.get('issued_at')
    start_day, start_time = _ban_date_time(began)
    expiry = ban.get('expires_at')
    end_day, end_time = _ban_date_time(expiry)
    end = f'{end_day} в {end_time}' if expiry else 'Навсегда'
    where = 'Вся сеть Openbot.Ai' if global_scope else 'Master Bot'
    admin = _ban_admin_username(ban, admin_username)
    reason = str(ban.get('reason') or 'Не указана').strip()[:500]
    return ('🚫 БЛОКИРОВКА ДОСТУПА\n'
            '━━━━━━━━━━━━━━━━━━\n'
            f'📍 Где: {where}\n'
            f'🔒 Тип: {kind}\n'
            f'📝 Причина: {reason}\n'
            f'👮 Администратор: {admin}\n\n'
            f'📅 Начало: {start_day} в {start_time}\n'
            f'⏳ Окончание: {end}\n\n'
            '📋 Подробности: /ban_info')


def _global_format_ban(ban, *, admin_username=None):
    return _ban_notice(
        ban, global_scope=True, admin_username=admin_username
    )


def _local_format_ban(ban, *, admin_username=None):
    return _ban_notice(
        ban, global_scope=False, admin_username=admin_username
    )


def _sync_global_ban_mirror(user_id, ban):
    """Обновляет локальное отображение после действий на центральном API."""
    return sync_global_status(user_id, global_ban=ban)


@bot.message_handler(commands=['global_ban'])
def global_ban_command(message):
    """Ограничение игр, торговли, наград во всех ботах сети."""
    try:
        uid, name, duration, reason = _global_command_args(message)
        # Передаём все параметры по имени, чтобы исключить путаницу
        # между duration и reason в разных версиях клиента.
        result = openbot.global_ban(
            user_id=uid,
            duration=duration,
            reason=reason,
            actor_id=message.from_user.id,
        )
        _sync_global_ban_mirror(uid, result)
        _global_notify(
            uid, _global_format_ban(
                result, admin_username=message.from_user.username
            )
        )
        bot.reply_to(message, f'✅ Игрок @{name} забанен.')
    except (APIError, ValueError) as exc:
        if "reason is required" in str(exc).lower():
            error_logger.error(
                "Несовместимая версия openbot_api при /global_ban: %s; module=%s",
                exc, getattr(openbot_api, "__file__", "unknown"),
            )
            bot.reply_to(
                message,
                "❌ Библиотека openbot_api не получила причину, хотя она есть "
                "в команде. Вероятно, бот запущен со старой версией пакета. "
                "Проверь путь к библиотеке в /logs.",
            )
        else:
            bot.reply_to(message, f"❌ {exc}")
    except Exception:
        error_logger.exception("Ошибка /global_ban")
        bot.reply_to(message, "❌ Ошибка глобального бана, проверь /logs")


@bot.message_handler(commands=['global_unban'])
def global_unban_command(message):
    """Удаляет только GLOBAL BAN; восстанавливает роль при отсутствии других банов."""
    try:
        uid, name = _global_command_args(message, lifting=True)
        if not openbot.global_unban(uid, actor_id=message.from_user.id):
            return bot.reply_to(message, '❌ У игрока нет GLOBAL BAN')
        # Метод возвращает True только при подтверждённом снятии на FastAPI.
        _sync_global_ban_mirror(uid, {'banned': False, 'kind': None})
        # Даже если зеркало не обновилось из-за временного сбоя, центральный бан снят.
        remaining = _punishment_get(uid)
        access = (
            '⚠️ Локальный бан остаётся активным. Проверь /ban_info.'
            if remaining else '🎮 Доступ восстановлен.'
        )
        _global_notify(uid, f'✅ GLOBAL BAN снят!\n{access}')
        bot.reply_to(message, f'✅ Глобальный бан с @{name} снят.')
    except (APIError, ValueError) as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /global_unban')
        bot.reply_to(message, '❌ Ошибка снятия бана, проверь /logs')


@bot.message_handler(commands=['global_hard_ban'])
def global_hard_ban_command(message):
    """Полное закрытие игровых функций сети, доступна только информация и поддержка."""
    try:
        uid, name, duration, reason = _global_command_args(message)
        result = openbot.global_hard_ban(
            user_id=uid,
            duration=duration,
            reason=reason,
            actor_id=message.from_user.id,
        )
        _sync_global_ban_mirror(uid, result)
        _global_notify(
            uid, _global_format_ban(
                result, admin_username=message.from_user.username
            )
        )
        bot.reply_to(message, f'✅ Игрок @{name} забанен.')
    except (APIError, ValueError) as exc:
        if "reason is required" in str(exc).lower():
            error_logger.error(
                "Несовместимая версия openbot_api при /global_hard_ban: %s; module=%s",
                exc, getattr(openbot_api, "__file__", "unknown"),
            )
            bot.reply_to(
                message,
                "❌ Библиотека openbot_api не получила причину, хотя она есть "
                "в команде. Вероятно, бот запущен со старой версией пакета. "
                "Проверь путь к библиотеке в /logs.",
            )
        else:
            bot.reply_to(message, f"❌ {exc}")
    except Exception:
        error_logger.exception("Ошибка /global_hard_ban")
        bot.reply_to(message, "❌ Ошибка глобального HARD BAN, проверь /logs")


@bot.message_handler(commands=['global_unhard_ban'])
def global_unhard_ban_command(message):
    """Удаляет только GLOBAL HARD BAN и обновляет отображение статуса."""
    try:
        uid, name = _global_command_args(message, lifting=True)
        if not openbot.global_unhard_ban(uid, actor_id=message.from_user.id):
            return bot.reply_to(message, '❌ У игрока нет GLOBAL HARD BAN')
        _sync_global_ban_mirror(uid, {'banned': False, 'kind': None})
        remaining = _punishment_get(uid)
        access = (
            '⚠️ Локальный бан остаётся активным. Проверь /ban_info.'
            if remaining else '🎮 Доступ восстановлен.'
        )
        _global_notify(uid, f'✅ GLOBAL HARD BAN снят!\n{access}')
        bot.reply_to(message, f'✅ Глобальный HARD BAN с @{name} снят.')
    except (APIError, ValueError) as exc:
        bot.reply_to(message, f'❌ {exc}')
    except Exception:
        error_logger.exception('Ошибка /global_unhard_ban')
        bot.reply_to(message, '❌ Ошибка снятия HARD BAN, проверь /logs')


@bot.message_handler(commands=['ban_info'])
def global_ban_information(message):
    """Владелец видит свои баны; админ может проверить /ban_info @username."""
    parts = (message.text or '').split(maxsplit=1)
    uid = message.from_user.id
    username = message.from_user.username or 'без_username'
    if len(parts) == 2 and parts[1].strip():
        if not _punish_admin(message):
            return bot.reply_to(message, '⛔ Проверять других игроков может администрация.')
        try:
            uid, username = _global_target(parts[1])
        except ValueError as exc:
            return bot.reply_to(message, f'❌ {exc}')

    notices = []
    failed = False
    try:
        central = openbot.get_global_ban(uid)
        if central.get('banned'):
            notices.append(_global_format_ban(central))
    except (APIError, OSError, ValueError):
        failed = True
    local = _punishment_get(uid)
    if local:
        notices.append(_local_format_ban(local))
    if notices:
        content = f'📋 Информация о блокировках @{username}\n\n' + (
            '\n\n──────────────\n\n'.join(notices)
        )
        if failed:
            content += '\n\n⚠️ Глобальный статус временно недоступен.'
    elif failed:
        content = '⚠️ Сейчас не удалось проверить глобальные блокировки.'
    else:
        content = f'✅ У @{username} нет активных блокировок.'
    bot.reply_to(message, content[:4000])


@bot.message_handler(commands=['appeal'])
def global_ban_appeal(message):
    bot.reply_to(message, '🛠 Система поддержки пока в разработке.')


@bot.message_handler(commands=['support'])
def support_placeholder(message):
    bot.reply_to(message, '🛠 Система поддержки пока в разработке.')


class GlobalBanMiddleware(BaseMiddleware):
    """Проверка центральных наказаний перед каждым сообщением и callback.

    При сбое API экономические операции запрещаются; уже совершённые
    Telegram Stars платежи всегда обрабатываются штатным обработчиком.
    """
    SOFT_CMDS = {
        '/start', '/help', '/profile', '/id_profile', '/my_titles',
        '/leaders', '/ban_info', '/appeal', '/get_status',
        '/calendar', '/menu',
    }
    HARD_CMDS = {'/ban_info', '/appeal', '/support'}
    SOFT_TEXT = {
        '👤 Профиль', '🎒 Инвентарь', '⚙️ Настройки',
        '⚙ Настройки ID', '⬅ Главное меню', '⬅ В главное меню',
        '⬅ Инвентарь', '📊 Лидеры', '🏆 Мои титулы',
        '📅 Календарь ноября', '📅 Календарь декабря',
    }
    SOFT_CALLBACKS = {
        'profile', 'settings_user', 'settings_id', 'back',
        'open_inventory', 'winter_titles', 'winter_leaders', 'leaders_refresh',
        'winter_badge', 'toggle_kb', 'toggle_notif', 'toggle_show_id',
        'ignore_click', 'nov_cal_open', 'nov_cal_refresh', 'nov_cal_back',
    }
    SOFT_PREFIXES = (
        'winter_title:', 'invpage_', 'emoji_info_',
        'emoji_equip_', 'emoji_unequip_',
    )

    def __init__(self):
        super().__init__()
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        user = getattr(update, 'from_user', None)
        if user is None:
            return None
        # Completed Stars payments must never be discarded.
        if getattr(update, 'successful_payment', None):
            return None
        is_callback = hasattr(update, 'data')
        value = (update.data or '') if is_callback else (getattr(update, 'text', '') or '').strip()
        command = value.split(maxsplit=1)[0].split('@')[0].lower() if value.startswith('/') else ''
        if command in ('/tech', '/reply_off', '/reply_on') or (is_callback and (value.startswith('tech:') or value == 'reply:to_inline')):
            return None  # Проверка прав проводится внутри /tech.
        try:
            ban = openbot.get_global_ban(user.id)
        except (APIError, OSError, ValueError):
            if command in self.HARD_CMDS:
                return None
            response = '⚠️ Openbot API временно недоступен. Действие не выполнено.'
            if is_callback:
                bot.answer_callback_query(update.id, response, show_alert=True)
            else:
                bot.send_message(update.chat.id, response)
            return CancelUpdate()
        kind = ban.get('kind') if ban.get('banned') else None
        try:
            mirrored = conn.execute(
                'SELECT global_ban_kind, status FROM users WHERE user_id=?',
                (user.id,),
            ).fetchone()
            if mirrored and (
                mirrored[0] != kind or (kind and mirrored[1] != 'banned')
            ):
                _sync_global_ban_mirror(user.id, ban)
        except sqlite3.Error:
            logging.getLogger('master_bot.status').exception(
                'Не удалось проверить локальную копию бана')
        if not ban.get('banned'):
            return None
        if is_callback:
            allowed = False  # Любое нажатие при активном бане показывает подробности
            if not allowed:
                bot.answer_callback_query(update.id, '🚫 Доступ ограничен')
                bot.send_message(update.message.chat.id, _global_format_ban(ban))
                return CancelUpdate()
        else:
            allowed = (command in self.HARD_CMDS or
                       (kind == 'global_ban' and
                        (command in self.SOFT_CMDS or value in self.SOFT_TEXT or
                         value.startswith(('⌨ Переключить', '🔔 Уведомления:',
                                           '🔕 Уведомления:', '👁 ID для других:',
                                           '🙈 ID для других:')))))
            if not allowed:
                bot.send_message(update.chat.id, _global_format_ban(ban))
                return CancelUpdate()
        return None

    def post_process(self, update, data, exception):
        pass


bot.setup_middleware(GlobalBanMiddleware())

class PunishmentMiddleware(BaseMiddleware):
    """Stop mutations for soft bans and all actions for hard bans."""
    SAFE_COMMANDS = {'/badge', '/my_titles', '/leaders', '/start', '/help', '/profile', '/id_profile', '/logs', '/ban',
                     '/unban', '/hard_ban', '/unhard_ban', '/ban_id', '/unban_id', '/get_status', '/ban_info', '/appeal', '/calendar', '/menu'}
    SAFE_TEXT = {'👤 Профиль', '🎒 Инвентарь', '⚙️ Настройки', '⚙ Настройки ID',
                 '⬅ Главное меню', '⬅ Инвентарь', '⬅ Главная',
                 '🏆 Лидеры', '📊 Лидеры', '🏆 Мои титулы', '🎄 Праздничное событие', '📅 Календарь ноября'}
    SAFE_CALLBACKS = {'winter_titles', 'winter_leaders', 'leaders_refresh', 'winter_badge', 'back', 'profile', 'open_inventory', 'settings_user',
                      'settings_id', 'toggle_kb', 'toggle_notif', 'toggle_show_id',
                      'ignore_click', 'nov_cal_open', 'nov_cal_refresh', 'nov_cal_back'}
    SAFE_PREFIXES = ('winter_title:', 'invpage_', 'emoji_info_', 'emoji_equip_', 'emoji_unequip_')

    def __init__(self):
        super().__init__()
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        person = getattr(update, 'from_user', None)
        if person is None:
            return None
        ban = _punishment_get(person.id)
        if not ban:
            return None
        hard = ban['kind'] == 'hard_ban'
        if hasattr(update, 'data'):
            key = update.data or ''
            allowed = False  # Любое нажатие при локальном бане показывает подробности
            if not allowed:
                bot.answer_callback_query(update.id, '🚫 Доступ ограничен')
                bot.send_message(update.message.chat.id, _local_format_ban(ban))
                return CancelUpdate()
        else:
            # A completed payment must always be fulfilled; don't confiscate purchases.
            if getattr(update, 'successful_payment', None):
                return None
            content = (getattr(update, 'text', '') or '').strip()
            cmd = content.split(maxsplit=1)[0].split('@')[0].lower() if content.startswith('/') else ''
            allowed = (cmd in {'/ban_info', '/appeal', '/support', '/tech', '/reply_off', '/reply_on'} or
                       (not hard and (cmd in self.SAFE_COMMANDS or
                                     content in self.SAFE_TEXT or
                                     content.startswith(('👕 ', '⌨ Переключить',
                                                         '🔔 Уведомления:', '🔕 Уведомления:',
                                                         '👁 ID для других:', '🙈 ID для других:')))))
            if not allowed:
                bot.send_message(update.chat.id, _local_format_ban(ban))
                return CancelUpdate()
        return None

    def post_process(self, update, data, exception):
        pass


bot.setup_middleware(PunishmentMiddleware())

# ================================================================
# 🛠 Частичные техработы. /tech — панель управления.
# Значение 1: функция отключена, 0: доступна.
# ================================================================
_TECH_AREAS = {
    'games': ('🎮 Игры', 'Игры временно закрыты на техработы.'),
    'cases': ('🎁 Кейсы', 'Кейсы временно закрыты на техработы.'),
    'market': ('🏪 Рынок', 'Рынок временно закрыт на техработы.'),
    'shop': ('🛒 Магазин', 'Магазин временно закрыт на техработы.'),
    'calendar': ('📅 Календарь', 'Календарь временно закрыт на техработы.'),
    'rewards': ('🎁 Подарки', 'Получение подарков временно отключено.'),
    'economy': ('🚨 Экономика', 'Экономические операции временно остановлены.'),
}


def _tech_get_states():
    # Отдельное соединение: бот обрабатывает сообщения параллельно.
    keys = {f'tech_{name}': name for name in _TECH_AREAS}
    with sqlite3.connect(DB_NAME, timeout=5) as db:
        rows = db.execute(
            'SELECT key, value FROM system_settings WHERE key LIKE ?', ('tech_%',)
        ).fetchall()
    states = {name: False for name in _TECH_AREAS}
    for key, value in rows:
        if key in keys:
            states[keys[key]] = str(value) == '1'
    return states


def _tech_disabled(area):
    states = _tech_get_states()
    return states.get(area, False) or (
        area in {'games', 'cases', 'market', 'shop', 'calendar', 'rewards'}
        and states['economy']
    )


TECH_REASONS = {
    'repair': '🛠 Технические работы',
    'bugs': '🐞 Исправление ошибок',
    'update': '🔄 Обновление раздела',
    'security': '🛡 Проверка безопасности',
    'event': '🎄 Подготовка зимнего события',
    'soon': '⏳ Скоро откроется',
}


def _tech_reason(area):
    with sqlite3.connect(DB_NAME, timeout=10) as db:
        row = db.execute('SELECT value FROM system_settings WHERE key=?',
                         ('tech_reason_' + area,)).fetchone()
    return row[0] if row else '🛠 Технические работы'


def _tech_set(area, disabled, reason=None, db=None):
    if area not in _TECH_AREAS:
        raise ValueError('Неизвестный раздел')
    if disabled and reason not in TECH_REASONS:
        raise ValueError('Сначала выбери причину закрытия')
    if db is None:
        with sqlite3.connect(DB_NAME, timeout=15) as connection:
            return _tech_set(area, disabled, reason, db=connection)
    db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (f'tech_{area}', '1' if disabled else '0'))
    if disabled:
        db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('tech_reason_' + area, TECH_REASONS[reason]))
    else:
        db.execute('DELETE FROM system_settings WHERE key=?', ('tech_reason_' + area,))


def _tech_set_bot(disabled, reason=None):
    if disabled and reason not in TECH_REASONS:
        raise ValueError('Сначала выбери причину закрытия')
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('maintenance', '1' if disabled else '0'))
        if disabled:
            db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       ('tech_reason_bot', TECH_REASONS[reason]))
        else:
            db.execute("DELETE FROM system_settings WHERE key='tech_reason_bot'")


def _tech_set_all(disabled, reason=None):
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute('BEGIN IMMEDIATE')
        for area in _TECH_AREAS:
            _tech_set(area, disabled, reason, db=db)


def _tech_restricted_section(area):
    states = _tech_get_states()
    if states.get('economy') and area in {'games', 'cases', 'market', 'shop', 'calendar', 'rewards'}:
        return 'economy'
    return area


def _tech_full_notice():
    return ('🛠 <b>MASTER BOT · ТЕХНИЧЕСКИЕ РАБОТЫ</b>\n'
            '━━━━━━━━━━━━━━━━━━\n\n'
            f'Причина: <b>{html.escape(_tech_reason("bot"))}</b>\n\n'
            'Доступ будет восстановлен после завершения работ.')


def _tech_reason_prompt(chat_id, area, panel_mid):
    if area != 'bot' and area != 'all' and area not in _TECH_AREAS:
        raise ValueError('Неизвестный раздел')
    title = ('🤖 Весь бот' if area == 'bot' else
             '🧰 Все разделы' if area == 'all' else _TECH_AREAS[area][0])
    kb = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(text,
            callback_data=f'tech:reason:{area}:{key}:{panel_mid}')
            for key, text in TECH_REASONS.items()]
    for i in range(0, len(btns), 2):
        kb.row(*btns[i:i+2])
    kb.add(types.InlineKeyboardButton('❌ Отмена', callback_data='tech:cancel'))
    return bot.send_message(chat_id, f'🔒 <b>ПРИЧИНА ЗАКРЫТИЯ</b>\n\n'
                            f'{html.escape(title)}\n\nВыбери причину:',
                            reply_markup=kb, parse_mode='HTML')


def _tech_delete_prompt(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except apihelper.ApiTelegramException:
        logging.getLogger('master_bot.tech').warning('Не удалось удалить меню причин %s', message_id)


def _tech_refresh_panel(chat_id, message_id):
    text, kb = _tech_panel()
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                              reply_markup=kb, parse_mode='HTML')
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def _tech_authorized(user, chat):
    if chat.type != 'private' or user is None:
        return False
    try:
        info = openbot.get_user(user.id)
        if not isinstance(info, dict) or not info.get('exists'):
            return False
        if str(info.get('status', '')).lower() not in {'owner', 'developer', 'admin'}:
            return False
        # Заблокированный сотрудник не может управлять техработами.
        try:
            ban = openbot.get_global_ban(user.id)
            if ban.get('banned'):
                return False
        except Exception:
            return False
        return not bool(_punishment_get(user.id))
    except Exception:
        return False


def _tech_panel():
    states = _tech_get_states()
    all_off = is_maintenance()
    kb = types.InlineKeyboardMarkup(row_width=1)
    lines = ['🛠 <b>MASTER BOT · ТЕХНИЧЕСКОЕ УПРАВЛЕНИЕ</b>',
             '━━━━━━━━━━━━━━━━━━', '',
             '🟢 Нажми, чтобы закрыть с причиной.',
             '🔴 Нажми, чтобы открыть.', '',
             f'{"🔴" if all_off else "🟢"} 🤖 Бот: {"Закрыт · " + html.escape(_tech_reason("bot")) if all_off else "Работает"}']
    kb.add(types.InlineKeyboardButton(f'🤖 Бот: {"🔴 Закрыт" if all_off else "🟢 Работает"}',
                                      callback_data='tech:bot'))
    for area, (title, _) in _TECH_AREAS.items():
        disabled = states[area]
        lines.append(f'{"🔴" if disabled else "🟢"} {html.escape(title)}: '
                     + (html.escape(_tech_reason(area)) if disabled else 'Работает'))
        kb.add(types.InlineKeyboardButton(f'{"🔴" if disabled else "🟢"} {title}',
                                          callback_data=f'tech:{area}'))
    kb.row(types.InlineKeyboardButton('✅ Открыть все', callback_data='tech:all:on'),
           types.InlineKeyboardButton('🔒 Закрыть все', callback_data='tech:all:off'))
    kb.add(types.InlineKeyboardButton('🔄 Обновить', callback_data='tech:refresh'))
    lines += ['', 'Закрытый раздел показывает игроку выбранную причину.',
              'Команда: <code>/tech</code>']
    return '\n'.join(lines), kb


@bot.message_handler(commands=['tech'])
def tech_command(message):
    if not _tech_authorized(message.from_user, message.chat):
        return bot.reply_to(message, '⛔ Только администрация в личных сообщениях.')
    parts = (message.text or '').split()
    if len(parts) == 1 or parts == ['/tech', 'status']:
        text, kb = _tech_panel()
        return bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='HTML')
    if len(parts) != 3 or parts[2] not in ('on', 'off'):
        return bot.reply_to(message, 'Формат: /tech bot|all|games|cases|market|shop|calendar|rewards|economy on|off')
    area, enable = parts[1], parts[2] == 'on'
    if area not in _TECH_AREAS and area not in ('bot', 'all'):
        return bot.reply_to(message, '❌ Неизвестный раздел.')
    if not enable:
        text, kb = _tech_panel()
        panel = bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        return _tech_reason_prompt(message.chat.id, area, panel.message_id)
    if area == 'bot':
        _tech_set_bot(False)
    elif area == 'all':
        _tech_set_all(False)
    else:
        _tech_set(area, False)
    text, kb = _tech_panel()
    bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='HTML')


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith('tech:'))
def tech_panel_callback(call):
    if not _tech_authorized(call.from_user, call.message.chat):
        return bot.answer_callback_query(call.id, '⛔ Нет доступа', show_alert=True)
    parts = (call.data or '').split(':')
    area = parts[1] if len(parts) > 1 else ''
    cid, mid = call.message.chat.id, call.message.message_id

    if area == 'cancel' and len(parts) == 2:
        bot.answer_callback_query(call.id, 'Отменено')
        return _tech_delete_prompt(cid, mid)
    if area == 'reason' and len(parts) == 5:
        _, _, target, reason_key, panel_id = parts
        if (target not in _TECH_AREAS and target not in ('all', 'bot')
                or reason_key not in TECH_REASONS or not panel_id.isdigit()):
            return bot.answer_callback_query(call.id, 'Некорректная причина', show_alert=True)
        if target == 'bot':
            _tech_set_bot(True, reason_key)
        elif target == 'all':
            _tech_set_all(True, reason_key)
        else:
            _tech_set(target, True, reason_key)
        bot.answer_callback_query(call.id, '🔒 Закрыто, причина сохранена')
        _tech_delete_prompt(cid, mid)
        if int(panel_id):
            return _tech_refresh_panel(cid, int(panel_id))
        return

    if area == 'bot' and len(parts) == 2:
        if not is_maintenance():
            _tech_reason_prompt(cid, 'bot', mid)
        else:
            _tech_set_bot(False)
            _tech_refresh_panel(cid, mid)
    elif area in _TECH_AREAS and len(parts) == 2:
        if not _tech_get_states()[area]:
            _tech_reason_prompt(cid, area, mid)
        else:
            _tech_set(area, False)
            _tech_refresh_panel(cid, mid)
    elif area == 'all' and len(parts) == 3 and parts[2] in ('on', 'off'):
        if parts[2] == 'off':
            _tech_reason_prompt(cid, 'all', mid)
        else:
            _tech_set_all(False)
            _tech_refresh_panel(cid, mid)
    elif area == 'refresh' and len(parts) == 2:
        _tech_refresh_panel(cid, mid)
    else:
        return bot.answer_callback_query(call.id, 'Неизвестная кнопка', show_alert=True)
    bot.answer_callback_query(call.id, '✅ Готово')


def _tech_area_for_update(update):
    if getattr(update, 'successful_payment', None) is not None:
        return None
    callback = hasattr(update, 'data')
    value = (update.data or '') if callback else (getattr(update, 'text', '') or '').strip()
    if callback:
        if value.startswith('tech:'):
            return None
        if value in {'game','back_to_games','start_clicker','game_click','start_number','start_wheel','start_dice','start_code','start_trader','back_to_clicker','shop_business','realestate'} or value.startswith(('game_','mnew:','duel_act_','ttt_pos_','ttt_level_','tot_pick_','graveyard_','trader_sell_')):
            return 'games'
        if value in {'cases'} or value.startswith(('casepage_','buy_case_','open_case_')):
            return 'cases'
        if value=='market' or value.startswith(('market_','emoji_market_')):
            return 'market'
        if value in {'shop','winter_titles','buy_coins','master_snow_shop','master_snowballs_exchange'} or value.startswith(('buy_title_','coin_titles_page:','master_snow_page:','master_snow_buy:','snowballs_exchange:','stars_buy_','cancel_stars_')):
            return 'shop'
        if value=='master_calendar_open' or value.startswith('master_cal:'):
            return 'calendar'
        if value.startswith('master_launch:'):
            return 'rewards'
        return None
    cmd = value.split(maxsplit=1)[0].split('@')[0].lower() if value.startswith('/') else ''
    if cmd=='/tech':return None
    if cmd in {'/calendar','/december'} or value=='📅 Календарь декабря':return 'calendar'
    if cmd in {'/sell_emoji'} or value=='🏪 Рынок' or value.startswith(('🏪 Выставить ','🛒 Купить ','❌ Снять лот #')):return 'market'
    if value in {'🎁 Кейсы','⬅ Предыдущие кейсы','Следующие кейсы ➡'} or value.startswith(('🔓 Открыть ','🛒 📦','🛒 🥈','🛒 🥇','🛒 💎','🛒 🔥','🛒 🌌','🛒 👑','🛒 🕳️')):return 'cases'
    if value in {'🎁 Зимняя лавка','🏆 Титулы','⭐ Магазин Stars','⬅ Магазин','❄️ 25 снежинок — 50 ⭐','👑 Титулы за монеты','❄️ Титулы за снежинки','☃️ Обмен снежков'} or value.startswith('🏆 '):return 'shop'
    if value in {'❄️ Зимние игры','🔮 Тайна снежного шара','❌⭕ Крестики-нолики','❄️ Снежинки vs ⛄ Снеговики','⛄ Битва со снежным големом','🎁 Подарок или сюрприз','🔎 Поиск зимних сокровищ','🧊 Ледяной лабиринт','🎄 Мастерская гирлянд','🐧 Экспедиция пингвина','🔔 Морозный шифр','🧊 Лабиринт','🎄 Гирлянды','🐧 Экспедиция'} or value.startswith(('🔒 ','🎄 ','🎁 Подарок ')) or value in {'⚔️ Атака','🛡️ Щит','🧪 Зелье','🔎 Искать'}:return 'games'
    if cmd in {'/give_coins','/take_coins','/gift','/level_up'}:return 'rewards'
    states=_tech_get_states()
    if (states['market'] or states['economy']) and update.from_user.id in pending_market and not value.startswith('/'):
        return 'market'
    if (states['games'] or states['economy']) and not value.startswith('/'):
        try:
            if get_active_game(update.from_user.id):return 'games'
        except sqlite3.Error:pass
    return None


class PartialTechMiddleware(BaseMiddleware):
    """Срабатывает на сообщениях и старых Inline-кнопках до обработчиков."""
    def __init__(self):
        super().__init__()
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        if getattr(update, 'successful_payment', None) is not None:
            return None
        area = _tech_area_for_update(update)
        if area is None or not _tech_disabled(area):
            return None
        # Owner, developer, admin и tester могут пользоваться закрытыми разделами.
        # GlobalBanMiddleware и PunishmentMiddleware по-прежнему проверяют наказания.
        staff_user = getattr(update, 'from_user', None)
        if staff_user is not None and _master_staff(staff_user.id):
            return None
        responsible = _tech_restricted_section(area)
        label = _TECH_AREAS[responsible][0]
        reason = html.escape(_tech_reason(responsible))
        msg = ('🛠 <b>РАЗДЕЛ ВРЕМЕННО ЗАКРЫТ</b>\n'
               '━━━━━━━━━━━━━━━━━━\n\n'
               f'📍 Раздел: {html.escape(label)}\n'
               f'📝 Причина: <b>{reason}</b>\n\n'
               'Другие доступные разделы продолжают работать.')
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton('🏠 Главное меню', callback_data='back'))
        if hasattr(update, 'data'):
            bot.answer_callback_query(update.id)
            try:
                bot.edit_message_text(msg, chat_id=update.message.chat.id,
                                      message_id=update.message.message_id,
                                      reply_markup=keyboard, parse_mode='HTML')
            except apihelper.ApiTelegramException as exc:
                if 'message is not modified' not in str(exc).lower():
                    bot.send_message(update.message.chat.id, msg, reply_markup=keyboard, parse_mode='HTML')
        else:
            bot.send_message(update.chat.id, msg, reply_markup=keyboard, parse_mode='HTML')
        return CancelUpdate()

    def post_process(self, update, data, exception):
        pass


bot.setup_middleware(PartialTechMiddleware())


# Master game availability switches, distinct from Random games.
MASTER_FEATURES = {
    'ttt': ('❌⭕ Крестики-нолики', 'game_ttt'),
    'maze': ('🧊 Ледяной лабиринт', 'mnew:maze:start'),
    'lights': ('🎄 Мастерская гирлянд', 'mnew:lights:start'),
    'penguin': ('🐧 Экспедиция пингвина', 'mnew:penguin:start'),
    'cipher': ('🔔 Морозный шифр', 'mnew:cipher:start'),
}

# Причины сохраняются в system_settings и не пропадают при перезапуске.
FEATURE_REASONS = {
    'tech': '🛠 Технические работы',
    'bugs': '🐞 Исправление ошибок',
    'update': '🔄 Обновление игры',
    'soon': '🕒 Скоро выйдет',
    'dev': '🚧 В разработке',
    'event': '🎄 Подготовка зимнего события',
}


def _master_feature_catalog():
    catalog = dict(MASTER_FEATURES)
    catalog.update({k: (v, f'mnew:{k}:start') for k, v in MASTER_UPCOMING_GAMES.items()})
    return catalog


def _master_feature_enabled(key):
    default = '1' if key in MASTER_FEATURES else '0'
    return _master_setting('feature_' + key, default) == '1'


def _master_can_use_game(key, user_id):
    """Закрытие через /features действует для игроков, но не для служебных ролей."""
    return _master_feature_enabled(key) or _master_staff(user_id)


def _master_feature_reason(key):
    default = '🕒 Скоро выйдет' if key in MASTER_UPCOMING_GAMES else '🛠 Временная блокировка'
    return _master_setting('feature_reason_' + key, default)


def _master_feature_closed_text(key):
    name = _master_feature_catalog().get(key, ('Игра', None))[0]
    return f'🔒 {name}\nПричина: {_master_feature_reason(key)}'


def _master_set_game_state(key, enabled, reason=None, *, db=None):
    if key not in _master_feature_catalog():
        raise ValueError('Неизвестная игра')
    if not enabled and reason not in FEATURE_REASONS:
        raise ValueError('Нужно выбрать причину блокировки')
    if db is None:
        with sqlite3.connect(DB_NAME, timeout=15) as db_connection:
            return _master_set_game_state(key, enabled, reason, db=db_connection)
    db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
               'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               ('feature_' + key, '1' if enabled else '0'))
    if enabled:
        db.execute('DELETE FROM system_settings WHERE key=?', ('feature_reason_' + key,))
    else:
        db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('feature_reason_' + key, FEATURE_REASONS[reason]))


def _master_features_set_all(enabled, reason=None):
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute('BEGIN IMMEDIATE')
        for key in _master_feature_catalog():
            _master_set_game_state(key, enabled, reason, db=db)


def _master_features_panel():
    kb = types.InlineKeyboardMarkup(row_width=1)
    lines = ['🎮 <b>MASTER BOT · УПРАВЛЕНИЕ ИГРАМИ</b>',
             '━━━━━━━━━━━━━━━━━━', '',
             'Нажми на 🟢, чтобы выбрать причину закрытия.',
             'Нажми на 🔒, чтобы сразу открыть игру.', '']
    for key, (name, _) in _master_feature_catalog().items():
        on = _master_feature_enabled(key)
        sign = '🟢' if on else '🔒'
        lines.append(f'{sign} {html.escape(name)}' +
                     ('' if on else f' — {html.escape(_master_feature_reason(key))}'))
        kb.add(types.InlineKeyboardButton(f'{sign} {name}', callback_data=f'features:toggle:{key}'))
    kb.row(types.InlineKeyboardButton('✅ Открыть все', callback_data='features:all:on'),
           types.InlineKeyboardButton('🔒 Закрыть все', callback_data='features:all:off'))
    kb.add(types.InlineKeyboardButton('🔄 Обновить', callback_data='features:refresh'))
    kb.add(types.InlineKeyboardButton('🏠 Главное меню', callback_data='back'))
    return '\n'.join(lines), kb


def _master_features_reason_prompt(chat_id, key, panel_mid):
    names = _master_feature_catalog()
    name = 'всех игр' if key == 'all' else names[key][0]
    kb = types.InlineKeyboardMarkup(row_width=2)
    reason_buttons = []
    for reason_key, reason_label in FEATURE_REASONS.items():
        reason_buttons.append(types.InlineKeyboardButton(
            reason_label, callback_data=f'features:reason:{key}:{reason_key}:{panel_mid}'))
    for start in range(0, len(reason_buttons), 2):
        kb.row(*reason_buttons[start:start + 2])
    kb.add(types.InlineKeyboardButton('❌ Отмена', callback_data='features:cancel'))
    return bot.send_message(chat_id, '🔒 <b>ПРИЧИНА БЛОКИРОВКИ</b>\n\n'
                            f'{html.escape(name)}\n\nВыбери причину:',
                            reply_markup=kb, parse_mode='HTML')


def _master_features_refresh(chat_id, mid):
    text, kb = _master_features_panel()
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=mid,
                              reply_markup=kb, parse_mode='HTML')
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


def _master_features_remove_prompt(chat_id, mid):
    try:
        bot.delete_message(chat_id, mid)
    except apihelper.ApiTelegramException:
        logging.getLogger('master_bot.features').warning('Не удалось удалить меню причин %s', mid)


@bot.message_handler(commands=['features'])
def master_features_command(message):
    if message.chat.type != 'private' or not _master_admin(message.from_user.id):
        return bot.reply_to(message, '⛔ Только администрация в ЛС.')
    args = (message.text or '').split()
    catalog = _master_feature_catalog()
    if len(args) == 3 and args[1] in catalog and args[2] in {'on', 'off'}:
        if args[2] == 'on':
            _master_set_game_state(args[1], True)
        text, kb = _master_features_panel()
        panel = bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='HTML')
        if args[2] == 'off':
            _master_features_reason_prompt(message.chat.id, args[1], panel.message_id)
        return
    if len(args) not in (1, 2) or (len(args) == 2 and args[1] != 'status'):
        return bot.reply_to(message, 'Используй /features — откроется панель управления.')
    text, kb = _master_features_panel()
    return bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='HTML')


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith('features:'))
def master_features_callback(call):
    if call.message.chat.type != 'private' or not _master_admin(call.from_user.id):
        return bot.answer_callback_query(call.id, '⛔ Нет доступа', show_alert=True)
    parts = (call.data or '').split(':')
    action = parts[1] if len(parts) > 1 else ''
    catalog = _master_feature_catalog()
    chat_id = call.message.chat.id
    mid = call.message.message_id

    if action == 'cancel' and len(parts) == 2:
        bot.answer_callback_query(call.id, 'Отменено')
        _master_features_remove_prompt(chat_id, mid)
        return

    if action == 'reason' and len(parts) == 5:
        _, _, key, reason, panel_mid = parts
        if (key != 'all' and key not in catalog) or reason not in FEATURE_REASONS or not panel_mid.isdigit():
            return bot.answer_callback_query(call.id, 'Некорректная причина', show_alert=True)
        if key == 'all':
            _master_features_set_all(False, reason)
        else:
            _master_set_game_state(key, False, reason)
        bot.answer_callback_query(call.id, '🔒 Игра закрыта; причина сохранена')
        _master_features_remove_prompt(chat_id, mid)
        _master_features_refresh(chat_id, int(panel_mid))
        return

    if action in {'toggle', 'soon'} and len(parts) == 3 and parts[2] in catalog:
        key = parts[2]
        if _master_feature_enabled(key):
            _master_features_reason_prompt(chat_id, key, mid)
            bot.answer_callback_query(call.id)
            return
        _master_set_game_state(key, True)
        bot.answer_callback_query(call.id, '✅ Игра открыта!')
        _master_features_refresh(chat_id, mid)
        return

    if action == 'all' and len(parts) == 3 and parts[2] in {'on', 'off'}:
        if parts[2] == 'off':
            _master_features_reason_prompt(chat_id, 'all', mid)
            bot.answer_callback_query(call.id)
            return
        _master_features_set_all(True)
        bot.answer_callback_query(call.id, '✅ Все игры открыты')
        _master_features_refresh(chat_id, mid)
        return

    if action == 'refresh' and len(parts) == 2:
        bot.answer_callback_query(call.id, '🔄 Обновлено')
        _master_features_refresh(chat_id, mid)
        return
    bot.answer_callback_query(call.id, 'Неизвестная кнопка', show_alert=True)

@bot.message_handler(commands=['testgame'])
def master_testgame_command(message):
    if message.chat.type!='private' or not _master_staff(message.from_user.id):
        return bot.reply_to(message,'⛔ Команда только для сотрудников.')
    parts=(message.text or '').split()
    if len(parts)!=2 or parts[1] not in _master_feature_catalog():
        return bot.reply_to(message,'Формат: /testgame ttt|maze|lights|penguin|cipher|aurora|express|fortress')
    if parts[1]=='ttt':
        return bot.send_message(message.chat.id, '❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\nВыбери сложность:',
                                reply_markup=ttt_level_markup(),parse_mode='HTML')
    return _mnew_start(message.from_user.id, message.chat.id, parts[1], force=True)

@bot.message_handler(commands=['help'])
def master_help_parity(message):
    role=_master_help_role(message.from_user.id)
    lines=['🎄 <b>MASTER BOT · ПОМОЩЬ</b>','',
           '❄️ <b>КОМАНДЫ ИГРОКА</b>',
           '/start — запуск', '/menu — меню', '/help — команды',
           '/profile — профиль', '/id_profile — Openbot ID',
           '/calendar, /december — календарь', '/my_titles — титулы',
           '/leaders — лидеры', '/game_stop — выход из игры',
           '/sell_emoji — рынок', '/bio — изменить био',
           '/feedback — отзыв', '/ban_info — проверить блокировку',
           '/appeal, /support — поддержка (в разработке)']
    if role in {'admin','developer','owner'}:
        lines+=['','🛡 <b>АДМИНИСТРАЦИЯ</b>',
          '/ban @username 7d Причина','/unban @username',
          '/hard_ban @username 7d Причина','/unhard_ban @username',
          '/global_ban @username 7d Причина','/global_unban @username',
          '/global_hard_ban @username 7d Причина','/global_unhard_ban @username',
          '/ban_info @username','/get_status @username [роль уровень]',
          '/sendall текст','/give_coins @username 100',
          '/take_coins @username 100','/gift @username 🎁',
          '/level_up @username 20','/tech — техработы',
          '/launch — релиз', '/features — панель управления играми',
          '/testgame ttt — проверка игр']
    if role in {'developer','owner'}:
        lines+=['','💻 <b>РАЗРАБОТКА</b>',
                '/reply_off — ограничить Reply обычным игрокам',
                '/reply_on — восстановить Reply','/logs — журнал ошибок',
                '/test_error — тест ошибок (по ID)']
    kb=types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
    bot.send_message(message.chat.id,'\n'.join(lines),reply_markup=kb,parse_mode='HTML')



# ============================================================
# ЧЕТЫРЕ НОВЫЕ ЗИМНИЕ ИГРЫ, ПО УМОЛЧАНИЮ ЗАКРЫТЫ.
# Данные партий — отдельная таблица основной Master DB.
# Старые игры и их награды больше не запускаются.
# ============================================================
import secrets as _mnew_secrets
import threading as _mnew_threading
_MNEW_LOCK=_mnew_threading.RLock()
_MNEW_QUIZ=(
    ('Что помогает пингвину сохранять тепло?', ('Густые перья','Песок','Листья'),0),
    ('Сколько лучей обычно у снежинки?', ('4','6','9'),1),
    ('Что тает при нагревании?', ('Лёд','Камень','Дерево'),0),
    ('Как называется замёрзшая вода?', ('Пыль','Лёд','Песок'),1),
    ('Какой месяц идёт после ноября?', ('Октябрь','Январь','Декабрь'),2),
    ('Что нужно для гирлянды?', ('Лампочки','Кирпичи','Лёд'),0),
)

def _mnew_init():
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS master_new_games ('
                   'user_id INTEGER PRIMARY KEY, game TEXT NOT NULL, '
                   'state TEXT NOT NULL, nonce TEXT NOT NULL)')
_mnew_init()

def _mnew_get(uid):
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        row=db.execute('SELECT game,state,nonce FROM master_new_games WHERE user_id=?',(uid,)).fetchone()
    if not row:return None
    try:return row[0],json.loads(row[1]),row[2]
    except (ValueError,TypeError):return None

def _mnew_store(uid,mode,state,nonce):
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('INSERT INTO master_new_games(user_id,game,state,nonce) VALUES(?,?,?,?) '
                   'ON CONFLICT(user_id) DO UPDATE SET game=excluded.game,state=excluded.state,nonce=excluded.nonce',
                   (uid,mode,json.dumps(state,ensure_ascii=False),nonce))

def _mnew_clear(uid):
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('DELETE FROM master_new_games WHERE user_id=?',(uid,))

def _mnew_button(mode,nonce,text,action):
    return types.InlineKeyboardButton(text,callback_data=f'mnew:{mode}:{nonce}:{action}')

def _mnew_render(mode, state, nonce, details=''):
    kb = types.InlineKeyboardMarkup(row_width=3)
    if mode == 'maze':
        text=(f'🧊 <b>ЛЕДЯНОЙ ЛАБИРИНТ</b>\n\n'
              f'🚪 Комната: {state["step"]+1}/4 · ❤️ Жизни: {state["lives"]}\n'
              'Выбери безопасный проход.')
        for n, v in enumerate(('⬅️ Левый','⬆️ Прямо','➡️ Правый')):
            kb.add(_mnew_button(mode,nonce,v,f'pick{n}'))
    elif mode == 'lights':
        pattern=state['pattern'];step=state['step']
        text=(f'🎄 <b>МАСТЕРСКАЯ ГИРЛЯНД</b>\n\n'
              f'✨ Собрано: {step}/4\n'
              f'Запомни порядок: <b>{" ".join(pattern)}</b>\n'
              f'Выбери цвет №{step+1}.')
        for n,v in enumerate(('🔴','🟢','🔵')):
            kb.add(_mnew_button(mode,nonce,v,f'pick{n}'))
    elif mode == 'penguin':
        text=(f'🐧 <b>ЭКСПЕДИЦИЯ ПИНГВИНА</b>\n\n'
              f'🏁 Пройдено: {state["distance"]}/4\n'
              f'⚡ Силы: {state["energy"]} · 🍪 Еда: {state["food"]}\n'
              'Доберись до станции, сохранив силы!')
        for name,action in (('🐾 Идти (-2 ⚡)','walk'),('🛷 Скользить (-3 ⚡, +2 шага)','slide'),('🍪 Перекусить (+3 ⚡)','eat')):
            kb.add(_mnew_button(mode,nonce,name,action))
    elif mode == 'cipher':
        question=_MNEW_QUIZ[state['questions'][state['step']]]
        text=(f'🔔 <b>МОРОЗНЫЙ ШИФР</b>\n\n'
              f'🔐 Загадка {state["step"]+1}/3 · ❤️ Ошибок осталось: {state["lives"]}\n\n'
              f'<b>{html.escape(question[0])}</b>')
        for n,v in enumerate(question[1]):
            kb.add(_mnew_button(mode,nonce,v,f'pick{n}'))
    elif mode == 'aurora':
        text=(f'🌌 <b>ПОЛЯРНОЕ СИЯНИЕ</b>\n\n'
              f'✨ Собрано света: {state["glow"]}/5\n'
              f'🌙 Ход: {state["step"]+1}/4\n'
              'Собери 5 единиц света за четыре хода. Яркая вспышка может погаснуть!')
        for name,action in (('🟢 Тихий луч (+1)','soft'),('🔵 Яркий луч (+2 или 0)','bright'),('🟣 Мощная вспышка (+3 или 0)','burst')):
            kb.add(_mnew_button(mode,nonce,name,action))
    elif mode == 'express':
        text=(f'🚂 <b>СНЕЖНЫЙ ЭКСПРЕСС</b>\n\n'
              f'🚉 Станций: {state["distance"]}/4 · ⏱ Время: {state["time"]}\n'
              f'⛽ Топливо: {state["fuel"]} · 🛢 Заправок: {state["refuels"]}\n'
              'Доставь поезд до четвёртой станции до конца времени.')
        for name,action in (('🚉 Обычный рейс (+1, -2⏱, -1⛽)','local'),
                            ('⚡ Экспресс (+2, -1⏱, -3⛽)','fast'),
                            ('🛢 Заправка (+3⛽, -1⏱)','refuel')):
            kb.add(_mnew_button(mode,nonce,name,action))
    elif mode == 'fortress':
        dangers=('🌨 Метель','⚔️ Штурм','⛄ Сугробы')
        danger=state['threats'][state['wave']]
        text=(f'🏰 <b>ЛЕДЯНАЯ КРЕПОСТЬ</b>\n\n'
              f'🛡 Стены: {state["walls"]} · 🌊 Волна: {state["wave"]+1}/3\n'
              f'Опасность: <b>{dangers[danger]}</b>\n'
              'Выбери защиту: грей от метели, отражай штурм щитом, убирай сугробы.')
        for n,v in enumerate(('🔥 Обогрев','🛡 Щит','🧹 Расчистка')):
            kb.add(_mnew_button(mode,nonce,v,f'pick{n}'))
    else:
        raise ValueError('Неизвестная игра')
    if details: text += '\n\n' + html.escape(details)
    kb.add(types.InlineKeyboardButton('⬅️ К зимним играм',callback_data='back_to_games'))
    return text, kb


def _mnew_start(uid, cid, mode, mid=None, force=False):
    if mode not in set(_master_feature_catalog()) - {'ttt'}:
        return None
    if not force and not _master_can_use_game(mode, uid):
        return bot.send_message(cid, _master_feature_closed_text(mode))
    nonce=_mnew_secrets.token_hex(4)
    if mode=='maze':state={'step':0,'lives':2,'safe':[random.randrange(3) for _ in range(4)]}
    elif mode=='lights':state={'pattern':[random.choice(['🔴','🟢','🔵']) for _ in range(4)],'step':0}
    elif mode=='penguin':state={'distance':0,'energy':9,'food':2}
    elif mode=='cipher':state={'questions':random.sample(range(len(_MNEW_QUIZ)),3),'step':0,'lives':2}
    elif mode=='aurora':state={'step':0,'glow':0}
    elif mode=='express':state={'distance':0,'time':6,'fuel':5,'refuels':1}
    else:state={'wave':0,'walls':2,'threats':[random.randrange(3) for _ in range(3)]}
    _mnew_store(uid,mode,state,nonce)
    text,kb=_mnew_render(mode,state,nonce)
    if mid is None:return bot.send_message(cid,text,parse_mode='HTML',reply_markup=kb)
    return bot.edit_message_text(text,chat_id=cid,message_id=mid,reply_markup=kb,parse_mode='HTML')


def _mnew_step(mode,state,action):
    # Возвращает (finished, won, description)
    if mode=='maze':
        if action not in ('pick0','pick1','pick2'):raise ValueError('Неверный проход')
        n=int(action[-1]);safe=state['safe'][state['step']]
        if n==safe:
            state['step']+=1
            return state['step']>=4,state['step']>=4,'✅ Проход найден!'
        state['lives']-=1
        return state['lives']<=0,False,'🧊 Скользкий путь! Потеряна жизнь.'
    if mode=='lights':
        if action not in ('pick0','pick1','pick2'):raise ValueError('Неверный цвет')
        if ('🔴','🟢','🔵')[int(action[-1])]!=state['pattern'][state['step']]:
            return True,False,'❌ Цвета перепутаны!'
        state['step']+=1
        return state['step']>=4,state['step']>=4,'✨ Верно!'
    if mode=='penguin':
        if action=='eat':
            if state['food']<=0:raise ValueError('Еда закончилась')
            state['food']-=1;state['energy']=min(10,state['energy']+3)
            return False,False,'🍪 Пингвин подкрепился.'
        if action not in ('walk','slide'):raise ValueError('Неверное действие')
        cost=2 if action=='walk' else 3
        if state['energy']<cost:raise ValueError('Не хватает сил. Попробуй перекусить!')
        state['energy']-=cost;state['distance']+=1 if action=='walk' else 2
        if state['distance']>=4:return True,True,'🏁 Станция найдена!'
        return state['energy']==0 and state['food']==0,False,'🐧 Вперёд, к станции!'
    if mode=='cipher':
        if action not in ('pick0','pick1','pick2'):raise ValueError('Неверный ответ')
        question=_MNEW_QUIZ[state['questions'][state['step']]]
        if int(action[-1])!=question[2]:state['lives']-=1
        state['step']+=1
        if state['lives']==0:return True,False,'❄️ Ошибок слишком много.'
        if state['step']>=3:return True,True,'🔔 Шифр разгадан!'
        return False,False,'✅ Следующая загадка!'
    if mode=='aurora':
        if action not in ('soft','bright','burst'):raise ValueError('Неизвестный луч')
        state['step']+=1
        if action=='soft':charge=1
        elif action=='bright':charge=2 if random.random()<0.75 else 0
        else:charge=3 if random.random()<0.5 else 0
        state['glow']+=charge
        if state['glow']>=5:return True,True,'🌌 Сияние засияло над небом!'
        if state['step']>=4:return True,False,'🌫 Сияние погасло — света не хватило.'
        return False,False,(f'✨ +{charge} света' if charge else '☁️ Луч скрылся за облаками.')
    if mode=='express':
        if action=='refuel':
            if state['refuels']==0:raise ValueError('Заправок больше нет')
            if state['time']<2:raise ValueError('Не хватит времени на заправку')
            state['refuels']-=1;state['fuel']+=3;state['time']-=1
            return False,False,'🛢 Поезд заправлен.'
        if action not in ('local','fast'):raise ValueError('Неизвестный рейс')
        fuel=1 if action=='local' else 3
        t=2 if action=='local' else 1
        if state['fuel']<fuel:raise ValueError('Не хватает топлива!')
        if state['time']<t:raise ValueError('Не хватает времени!')
        state['fuel']-=fuel;state['time']-=t
        state['distance']+=1 if action=='local' else 2
        if state['distance']>=4:return True,True,'🚂 Пассажиры доставлены!'
        if state['time']==0:return True,False,'⌛ Время вышло!'
        return False,False,'🚉 Следующая станция всё ближе!'
    if mode=='fortress':
        if action not in ('pick0','pick1','pick2'):raise ValueError('Неверная защита')
        matched=int(action[-1])==state['threats'][state['wave']]
        if not matched:state['walls']-=1
        state['wave']+=1
        if state['walls']<=0:return True,False,'🏰 Крепость потеряла защиту.'
        if state['wave']>=3:return True,True,'🏆 Все три волны отражены!'
        return False,False,('🛡 Атака отражена!' if matched else '⚠️ Стена повреждена!')
    raise ValueError('Неизвестная игра')

@bot.callback_query_handler(func=lambda c:bool(c.data) and c.data.startswith('mnew:'))
def master_new_games_callback(call):
    parts=(call.data or '').split(':',3)
    if len(parts)<3:return bot.answer_callback_query(call.id,'Неизвестная кнопка')
    mode=parts[1]
    if mode=='locked':
        key=parts[2]
        if key not in _master_feature_catalog():
            return bot.answer_callback_query(call.id,'Неизвестная игра',show_alert=True)
        if not _master_can_use_game(key, call.from_user.id):
            return bot.answer_callback_query(call.id,_master_feature_closed_text(key)[:190],show_alert=True)
        # Даже старые сообщения с замочками работают для сотрудников.
        bot.answer_callback_query(call.id, '🧪 Тестирование игры')
        if key == 'ttt':
            return bot.edit_message_text(
                '❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\n\nВыбери сложность ИИ:',
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                reply_markup=ttt_level_markup(), parse_mode='HTML')
        return _mnew_start(call.from_user.id,call.message.chat.id,key,
                           call.message.message_id)
    if mode not in set(_master_feature_catalog()) - {'ttt'}:
        return bot.answer_callback_query(call.id,'Неизвестная игра',show_alert=True)
    if not _master_can_use_game(mode, call.from_user.id):
        return bot.answer_callback_query(call.id,_master_feature_closed_text(mode)[:190],show_alert=True)
    if len(parts)==3 and parts[2]=='start':
        # Старые и новые кнопки запуска уважают служебный доступ.
        bot.answer_callback_query(call.id)
        return _mnew_start(call.from_user.id,call.message.chat.id,mode,call.message.message_id)
    if len(parts)!=4:return bot.answer_callback_query(call.id,'Неверная кнопка')
    with _MNEW_LOCK:
        uid=call.from_user.id
        game=_mnew_get(uid)
        if not game or game[0]!=mode or game[2]!=parts[2]:
            return bot.answer_callback_query(call.id,'⏳ Это старая партия. Начни заново.',show_alert=True)
        state=game[1]
        try: finished,won,note=_mnew_step(mode,state,parts[3])
        except ValueError as exc:return bot.answer_callback_query(call.id,str(exc),show_alert=True)
        if finished:
            _mnew_clear(uid)
            kb=types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton('🔄 Играть снова',callback_data=f'mnew:{mode}:start'))
            kb.add(types.InlineKeyboardButton('⬅️ К играм',callback_data='back_to_games'))
            if won:
                rewards={'maze':(75,25),'lights':(90,30),'penguin':(80,25),'cipher':(100,35),
                         'aurora':(85,30),'express':(90,30),'fortress':(100,40)}
                coins,xp=rewards[mode]
                snowballs={'maze':2,'lights':3,'penguin':2,'cipher':3,
                           'aurora':3,'express':3,'fortress':4}[mode]
                add_coins(uid,coins);add_xp(uid,xp);add_snowballs(uid,snowballs)
                text=(f'🎉 <b>ПОБЕДА!</b>\n{html.escape(note)}\n\n'
                      f'💰 +{coins} монет · ⭐ +{xp} XP · ☃️ +{snowballs} снежков')
            else:text=f'❄️ <b>ПАРТИЯ ЗАВЕРШЕНА</b>\n{html.escape(note)}\nПопробуй ещё раз!'
        else:
            newnonce=_mnew_secrets.token_hex(4)
            _mnew_store(uid,mode,state,newnonce)
            text,kb=_mnew_render(mode,state,newnonce,note)
        bot.answer_callback_query(call.id)
        try:bot.edit_message_text(text,chat_id=call.message.chat.id,message_id=call.message.message_id,
                                  parse_mode='HTML',reply_markup=kb)
        except apihelper.ApiTelegramException as exc:
            if 'message is not modified' not in str(exc).lower():raise



def _snowballs_exchange_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    for snowballs, snowflakes in ((10, 1), (50, 5), (100, 10)):
        kb.add(types.InlineKeyboardButton(
            f'☃️ {snowballs} → ❄️ {snowflakes}', callback_data=f'snowballs_exchange:{snowballs}'))
    kb.add(types.InlineKeyboardButton('⬅️ В зимнюю лавку', callback_data='shop'))
    return (f'☃️ <b>ОБМЕН СНЕЖКОВ</b>\n\n'
            f'Твои снежки: <b>{get_snowballs(uid)}</b> ☃️\n'
            f'Твои снежинки: <b>{get_snowflakes(uid)}</b> ❄️\n\n'
            'Играй, получай снежки и меняй их на снежинки.'), kb


def _snowballs_convert(uid, amount):
    if amount not in (10, 50, 100):
        return False
    # Списание и начисление разных валют — одна транзакция.
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        db.execute('ATTACH DATABASE ? AS winter', (master_winter_db.get_db_path(),))
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('UPDATE users SET snowballs=snowballs-? '
                         'WHERE user_id=? AND COALESCE(snowballs,0)>=?',
                         (amount, uid, amount))
        if row.rowcount != 1:
            return False
        db.execute('INSERT INTO winter.players(user_id,snowflakes,created_at) VALUES(?,?,?) '
                   'ON CONFLICT(user_id) DO UPDATE SET snowflakes=snowflakes+excluded.snowflakes',
                   (uid, amount // 10, datetime.now(timezone.utc).isoformat()))
    return True


@bot.callback_query_handler(func=lambda c: (c.data or '') == 'master_snowballs_exchange'
                            or (c.data or '').startswith('snowballs_exchange:'))
def master_snowballs_callback(call):
    uid = call.from_user.id
    if call.data.startswith('snowballs_exchange:'):
        try:
            amount = int(call.data.partition(':')[2])
        except (ValueError, TypeError):
            return bot.answer_callback_query(call.id, 'Неверный обмен', show_alert=True)
        if not _snowballs_convert(uid, amount):
            return bot.answer_callback_query(call.id, '☃️ Недостаточно снежков', show_alert=True)
        bot.answer_callback_query(call.id, f'✅ Получено {amount // 10} ❄️', show_alert=True)
    else:
        bot.answer_callback_query(call.id)
    caption, kb = _snowballs_exchange_menu(uid)
    try:
        bot.edit_message_text(caption, chat_id=call.message.chat.id, message_id=call.message.message_id,
                              reply_markup=kb, parse_mode='HTML')
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    uid = call.from_user.id
    cid = call.message.chat.id
    mid = call.message.message_id
    # Старые кнопки больше не запускают прежние игры (включая награды).
    legacy = {'game_guess_num','game_duel','game_trick_or_treat','game_graveyard',
              'start_number','start_wheel','start_dice','start_code','start_trader'}
    if call.data in legacy or (call.data or '').startswith(('duel_act_','tot_pick_','graveyard_')):
        return bot.answer_callback_query(call.id,'🔒 Эта игра заменена. Новые игры скоро выйдут!',show_alert=True)
    # Блокировка действует также на старые доски и кнопки сложности.
    if (call.data == 'game_ttt' or (call.data or '').startswith(('ttt_level_', 'ttt_pos_', 'ttt_done'))):
        if not _master_can_use_game('ttt', uid):
            return bot.answer_callback_query(call.id, _master_feature_closed_text('ttt')[:190], show_alert=True)
    if call.data == 'master_calendar_open':
        text,kb=_master_december_calendar(user_id)
        bot.edit_message_text(text,chat_id=cid,message_id=mid,reply_markup=kb,parse_mode='HTML')
        return bot.answer_callback_query(call.id)
    if call.data.startswith('master_cal:'):
        return master_calendar_cb(call)
    if call.data.startswith('master_launch:'):
        return master_launch_cb(call)
    if call.data == 'master_titles_open':
        names=_master_owned_titles(user_id)
        kb=types.InlineKeyboardMarkup(row_width=1)
        for key,name in names.items():kb.add(types.InlineKeyboardButton(name,callback_data=f'master_title:{key}'))
        kb.add(types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
        bot.edit_message_text('🏆 <b>МОИ ТИТУЛЫ</b>',chat_id=cid,message_id=mid,reply_markup=kb,parse_mode='HTML')
        return bot.answer_callback_query(call.id)
    if call.data.startswith('master_title:'):
        return master_title_cb(call)
    if call.data == 'master_leaders_open':
        return master_leaders_callback(call)
    if call.data == 'master_snow_shop' or (call.data or '').startswith('master_snow_page:'):
        return master_snow_shop_callback(call)
    if call.data.startswith('master_snow_buy:'):
        return master_snow_buy_callback(call)
    reason = check_ban(uid)
    
    if openbot.is_globally_banned(uid):
        bot.answer_callback_query(call.id, "❄️ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.", show_alert=True)
        return
    if reason:
        bot.answer_callback_query(call.id, f"🚫 Вы заблокированы!\nПричина: {reason}", show_alert=True)
        return
    
    if call.data == 'game':
        _mnew_clear(user_id)
        delete_active_game(user_id)
        bot.edit_message_text(
            _master_games_menu_text(),
            chat_id=cid, message_id=mid,
            reply_markup=game_mune(user_id), parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return  # Было ДВА редактирования одного сообщения: больше не повторяем
    
    elif call.data == 'back':
        bot.edit_message_text(
            f'👋 Привет, {call.from_user.first_name}\nДобро пожаловать!',
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=main_mune()
        )
        bot.answer_callback_query(call.id)
    elif call.data == 'case':
        pass

    elif call.data == 'buy_coins':
        bot.edit_message_text(
            "⭐ <b>МАГАЗИН ЗА STARS</b>\n\n"
            "Выбери покупку. Оплата проходит через Telegram Stars (⭐).\n\n"
            "💰 10 000 000 монет\n"
            "❄️ 25 снежинок\n"
            "⭐ Прокачка игрового уровня до 20",
            chat_id=cid,
            message_id=mid,
            reply_markup=stars_shop_menu(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)

    elif call.data == 'stars_buy_coins_10m':
        send_stars_invoice(call, "coins_10m")
        bot.answer_callback_query(call.id)

    elif call.data == 'stars_buy_keys_5':
        send_stars_invoice(call, "snowflakes_25")
        bot.answer_callback_query(call.id)

    elif call.data == 'stars_buy_level_20':
        send_stars_invoice(call, "level_20")
        bot.answer_callback_query(call.id)

    elif call.data == 'winter_titles' or (call.data or '').startswith('coin_titles_page:'):
        page = 0
        if call.data.startswith('coin_titles_page:'):
            try:
                page = int(call.data.partition(':')[2])
            except (ValueError, TypeError):
                return bot.answer_callback_query(call.id, 'Неверная страница', show_alert=True)
        bot.answer_callback_query(call.id)
        _titles_edit_message(call, winter_titles_text(user_id, page=page),
                             winter_titles_menu(page, user_id))

    elif call.data.startswith('buy_title_'):
        title_key = call.data[len('buy_title_'):]
        ok, msg = buy_winter_title(user_id, title_key)
        bot.answer_callback_query(call.id, msg[:190], show_alert=True)
        if ok:
            page = list(WINTER_TITLES).index(title_key) // TITLE_CATALOG_PAGE_SIZE
            _titles_edit_message(call, winter_titles_text(user_id, page=page),
                                 winter_titles_menu(page, user_id))

    elif call.data.startswith("cancel_stars_"):
        invoice_message_id = int(call.data.replace("cancel_stars_", ""))

        try:
            bot.delete_message(
                call.message.chat.id,
                invoice_message_id
            )
        except Exception:
            pass

        try:
            bot.delete_message(
                call.message.chat.id,
                call.message.message_id
            )
        except Exception:
            pass

        bot.answer_callback_query(
            call.id,
            "❌ Оплата отменена"
        )
    elif call.data == "shop":
        bot.edit_message_text(_winter_shop_front(user_id), chat_id=cid, message_id=mid,
                              reply_markup=shop_mune(), parse_mode="HTML")
        bot.answer_callback_query(call.id)
        
    elif call.data == 'profile':
        user = get_user(user_id)
    
    # 1. Заменили uid на user_id
        global_data = openbot.get_id(user_id) 

    # 2. Достаем надетый предмет отдельно, чтобы он работал всегда
        equipped = get_equipped_emoji(user_id)

        if global_data:
            g_name = global_data[2]
            g_tag = global_data[5] or "Не установлен"
            g_status_key = global_data[6]
            base_name = g_name
        else:
            base_name = user[2] if user else "Игрок"
            g_tag = "Отсутствует"

    # 3. Объединяем эмоджи и имя для обоих случаев
        full_name = f"{equipped} {base_name}".strip()

        status_key = user[3] if user else "user"
        display_status = STATUS.get(status_key, "👤 Игрок")
        title = PROFILE_TITLES.get(user[4] if user else "none", "Без титула")
        
        if user:
            profile_text = f"""<b>ПРОФИЛЬ</b>
━━━━━━━━━━━━━━━━━━
👤 <b>Имя в сети:</b> {full_name}
🆔 <b>Ваш Тег:</b> <code>{g_tag}</code>
🎭 <b>Статус:</b> {display_status}
🎖 <b>Игровой титул:</b> {title}

📊 <b>ИГРОВАЯ СТАТИСТИКА:</b>
⭐ <b>Уровень:</b> {user[7]}
✨ <b>Опыт:</b> {user[8]}
💰 <b>Монеты:</b> {user[5]} 💰
☃️ <b>Снежки:</b> {get_snowballs(user_id)}
━━━━━━━━━━━━━━━━━━━
"""
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
               types.InlineKeyboardButton('⚙ Настройки ID', callback_data='settings_id'),
               types.InlineKeyboardButton('⬅ В главное меню', callback_data='back')
            )
            bot.edit_message_text(
                profile_text, 
                chat_id=user_id, 
                message_id=call.message.message_id, 
                reply_markup=markup, 
                parse_mode="HTML"
            )

        bot.answer_callback_query(call.id)

    # --- ОБНОВЛЕННЫЙ ПРОФИЛЬ С БЛОКАМИ <blockquote> И ПРОГРЕСС-БАРОМ ---
    elif call.data == 'profile':
        user = get_user(user_id)
        global_data = openbot.get_id(uid)

        if global_data:
            g_name = global_data[2]
            equipped = get_equipped_emoji(user_id)
            g_tag = global_data[5] or "Не установлен"
            g_status_key = global_data[6]
            full_name = f"{equipped} {g_name}".strip()
        else:
            full_name = user[2] if user else "Игрок"
            g_tag = "Отсутствует"

        display_status = STATUS.get(user[3] if user else "👤 Игрок")
        title = PROFILE_TITLES.get(user[4] if user else "none", "Без титула")
            
        if user:
            profile_text = f"""<b>ПРОФИЛЬ</b>
━━━━━━━━━━━━━━━━━━
👤 <b>Имя в сети:</b>{full_name}
🆔 <b>Ваш Тег:</b> <code>{g_tag}</code>
🎭 <b>Статус:</b> {display_status}
🎖 <b>Игровой титул:</b> {title}

📊 <b>ИГРОВАЯ СТАТИСТИКА:</b>
⭐ <b>Уровень:</b> {user[7]}
✨ <b>Опыт:</b> {user[8]}
💰 <b>Монеты:</b> {user[5]} 💰
☃️ <b>Снежки:</b> {get_snowballs(user_id)}
━━━━━━━━━━━━━━━━━━━
"""
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton('⚙ Настройки ID', callback_data='settings_id'),
                types.InlineKeyboardButton('⬅ В главное меню', callback_data='back')
            )
            bot.edit_message_text(profile_text, chat_id=user_id, message_id=call.message.message_id, 
                                reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
    
    # --- 🏪 РЫНОК ---
    elif call.data == 'market':
        show_market(cid, mid, user_id, page=1)
        bot.answer_callback_query(call.id)

    elif call.data.startswith('market_page_'):
        try:
            page = int(call.data.split('_')[-1])
        except ValueError:
            page = 1
        show_market(cid, mid, user_id, page=page)
        bot.answer_callback_query(call.id)

    elif call.data == 'market_my':
        show_my_market_lots(cid, mid, user_id)
        bot.answer_callback_query(call.id)

    elif call.data.startswith('market_buy_'):
        try:
            lot_id = int(call.data[len('market_buy_'):])
        except ValueError:
            bot.answer_callback_query(call.id, "❌ Ошибка лота!", show_alert=True)
            return

        cursor = conn.cursor()
        cursor.execute("""
            SELECT seller_id, item_type, item_value, price
            FROM auction
            WHERE lot_id = ?
            LIMIT 1
        """, (lot_id,))
        lot = cursor.fetchone()

        if not lot:
            bot.answer_callback_query(call.id, "❌ Лот уже продан или снят!", show_alert=True)
            show_market(cid, mid, user_id, page=1)
            return

        seller_id, item_type, emoji, price = lot

        if seller_id == user_id:
            bot.answer_callback_query(call.id, "❌ Нельзя купить собственный лот!", show_alert=True)
            return

        if item_type != 'emoji':
            bot.answer_callback_query(call.id, "❌ Этот тип товара пока не поддерживается!", show_alert=True)
            return

        cursor.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
        buyer_row = cursor.fetchone()
        buyer_coins = buyer_row[0] if buyer_row else 0

        if buyer_coins < price:
            bot.answer_callback_query(
                call.id,
                f"❌ Недостаточно монет! Нужно {price} 🪙",
                show_alert=True
            )
            return

        # Повторно удаляем именно этот лот: если два человека нажали одновременно,
        # только первый успешный UPDATE получит товар.
        cursor.execute("""
            DELETE FROM auction
            WHERE lot_id = ?
              AND seller_id = ?
              AND price = ?
        """, (lot_id, seller_id, price))

        if cursor.rowcount != 1:
            conn.rollback()
            bot.answer_callback_query(call.id, "❌ Лот уже купили!", show_alert=True)
            show_market(cid, mid, user_id, page=1)
            return

        cursor.execute("""
            UPDATE users
            SET coins = coins - ?
            WHERE user_id = ?
              AND coins >= ?
        """, (price, user_id, price))

        if cursor.rowcount != 1:
            conn.rollback()
            bot.answer_callback_query(call.id, "❌ Недостаточно монет!", show_alert=True)
            show_market(cid, mid, user_id, page=1)
            return

        cursor.execute("""
            UPDATE users
            SET coins = coins + ?
            WHERE user_id = ?
        """, (price, seller_id))

        cursor.execute("""
            INSERT INTO inventory (user_id, item_type, item_value, is_active)
            VALUES (?, 'emoji', ?, 0)
        """, (user_id, emoji))

        conn.commit()
        notify_market_sale(seller_id, user_id, emoji, price)

        bot.answer_callback_query(
            call.id,
            f"🎉 Куплено {emoji} за {price} 🪙!",
            show_alert=True
        )
        show_market(cid, mid, user_id, page=1)

    elif call.data.startswith('market_cancel_'):
        try:
            lot_id = int(call.data[len('market_cancel_'):])
        except ValueError:
            bot.answer_callback_query(call.id, "❌ Ошибка лота!", show_alert=True)
            return

        cursor = conn.cursor()
        cursor.execute("""
            SELECT item_value, price
            FROM auction
            WHERE lot_id = ?
              AND seller_id = ?
              AND item_type = 'emoji'
            LIMIT 1
        """, (lot_id, user_id))
        lot = cursor.fetchone()

        if not lot:
            bot.answer_callback_query(call.id, "❌ Лот не найден!", show_alert=True)
            show_my_market_lots(cid, mid, user_id)
            return

        emoji, price = lot

        cursor.execute("""
            DELETE FROM auction
            WHERE lot_id = ?
              AND seller_id = ?
        """, (lot_id, user_id))

        if cursor.rowcount != 1:
            conn.rollback()
            bot.answer_callback_query(call.id, "❌ Лот уже снят!", show_alert=True)
            show_my_market_lots(cid, mid, user_id)
            return

        cursor.execute("""
            INSERT INTO inventory (user_id, item_type, item_value, is_active)
            VALUES (?, 'emoji', ?, 0)
        """, (user_id, emoji))

        conn.commit()

        bot.answer_callback_query(
            call.id,
            f"↩️ {emoji} возвращён в инвентарь!",
            show_alert=True
        )
        show_my_market_lots(cid, mid, user_id)

    # --- ИНВЕНТАРЬ И ПАГИНАЦИЯ ---
    elif call.data == 'open_inventory':
        show_inventory_ui(call.message.chat.id, call.message.message_id, user_id, page=1)
        bot.answer_callback_query(call.id)

    elif call.data.startswith('invpage_'):
        page = int(call.data.split('_')[1])
        show_inventory_ui(call.message.chat.id, call.message.message_id, user_id, page=page)
        bot.answer_callback_query(call.id)

    elif call.data == 'ignore_click':
        bot.answer_callback_query(call.id)

    elif call.data.startswith('emoji_info_'):
        emoji=call.data[len('emoji_info_'):]; show_emoji_card(cid,mid,user_id,emoji); bot.answer_callback_query(call.id)

    elif call.data.startswith('emoji_equip_'):
        emoji=call.data[len('emoji_equip_'):]; cursor=conn.cursor()
        cursor.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1",(user_id,emoji))
        if not cursor.fetchone(): bot.answer_callback_query(call.id,"❌ Эмоджи не найдено!",show_alert=True); return
        cursor.execute("UPDATE users SET equipped_item=? WHERE user_id=?",(emoji,user_id)); conn.commit()
        bot.answer_callback_query(call.id,f"👕 {emoji} теперь надето!"); show_emoji_card(cid,mid,user_id,emoji)

    elif call.data.startswith('emoji_unequip_'):
        emoji=call.data[len('emoji_unequip_'):]; cursor=conn.cursor(); cursor.execute("UPDATE users SET equipped_item='' WHERE user_id=? AND equipped_item=?",(user_id,emoji)); conn.commit()
        bot.answer_callback_query(call.id,"👕 Эмоджи снято!"); show_emoji_card(cid,mid,user_id,emoji)

    elif call.data.startswith('emoji_sell_'):
        emoji=call.data[len('emoji_sell_'):]; price=get_emoji_price(emoji); cursor=conn.cursor()
        cursor.execute("SELECT id FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1",(user_id,emoji)); row=cursor.fetchone()
        if not row: bot.answer_callback_query(call.id,"❌ Эмоджи не найдено!",show_alert=True); return
        cursor.execute("DELETE FROM inventory WHERE id=?",(row[0],)); cursor.execute("SELECT COUNT(*) FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=?",(user_id,emoji)); remaining=cursor.fetchone()[0]
        if remaining==0: cursor.execute("UPDATE users SET equipped_item='' WHERE user_id=? AND equipped_item=?",(user_id,emoji))
        cursor.execute("UPDATE users SET coins=coins+? WHERE user_id=?",(price,user_id)); conn.commit()
        bot.answer_callback_query(call.id,f"💰 Продано за {price} 🪙!",show_alert=True); show_inventory_ui(cid,mid,user_id,1)

    elif call.data.startswith('emoji_market_'):
        emoji=call.data[len('emoji_market_'):]; cursor=conn.cursor(); cursor.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1",(user_id,emoji))
        if not cursor.fetchone(): bot.answer_callback_query(call.id,"❌ Эмоджи не найдено!",show_alert=True); return
        pending_market[user_id]=emoji; bot.answer_callback_query(call.id)
        bot.send_message(cid,f"🏪 <b>Выставление {emoji} на рынок</b>\n\nВведи цену в монетах. Например: <code>1500</code>",parse_mode="HTML")

    elif call.data == 'cases':
        bot.edit_message_text(
            cases_text(user_id, 0),
            chat_id=cid,
            message_id=mid,
            reply_markup=build_cases_keyboard(user_id, 0),
            parse_mode="HTML",
        )
        bot.answer_callback_query(call.id)

    elif call.data.startswith('casepage_'):
        page = int(call.data[len('casepage_'):])
        bot.edit_message_text(
            cases_text(user_id, page),
            chat_id=cid,
            message_id=mid,
            reply_markup=build_cases_keyboard(user_id, page),
            parse_mode="HTML",
        )
        bot.answer_callback_query(call.id)

    elif call.data.startswith('buy_case_'):
        parts = call.data[len('buy_case_'):].rsplit('_', 1)
        case_key = parts[0]
        page = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
        ok, msg = buy_case(user_id, case_key)
        bot.answer_callback_query(call.id, msg, show_alert=True)
        if ok:
            bot.edit_message_text(
                cases_text(user_id, page),
                chat_id=cid,
                message_id=mid,
                reply_markup=build_cases_keyboard(user_id, page),
                parse_mode="HTML",
            )

    elif call.data.startswith('open_case_'):
        parts = call.data[len('open_case_'):].rsplit('_', 1)
        case_key = parts[0]
        page = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
        result, msg = open_case(user_id, case_key)
        if result is None:
            bot.answer_callback_query(call.id, msg, show_alert=True)
            return

        c_info = CASES[case_key]
        count_left = get_case_count(user_id, case_key)
        bot.edit_message_text(
            f"🎁 <b>КЕЙС ОТКРЫТ!</b>\n\n"
            f"{c_info['name']}\n\n"
            f"✨ <b>ТЕБЕ ВЫПАЛО:</b>\n{msg}\n\n"
            f"📦 Осталось таких кейсов: <b>{count_left}</b>\n\n"
            f"{balance_line(user_id)}",
            chat_id=cid,
            message_id=mid,
            reply_markup=build_cases_keyboard(user_id, page),
            parse_mode="HTML",
        )
        bot.answer_callback_query(call.id, "🎁 Кейс открыт!", show_alert=True)

    elif call.data == 'winter_chest':

        keys = get_snowflakes(user_id)

        markup = types.InlineKeyboardMarkup(row_width=1)

        markup.add(
            types.InlineKeyboardButton(
                "❄️ Открыть сундук",
                callback_data="open_winter_chest")
        ) 

        markup.add(
            types.InlineKeyboardButton(
                "🔙 Назад к играм",
                callback_data="back_to_games"
            )
        )

        bot.edit_message_text(
            f"🕯️ <b>ЗИМНИЙ СУНДУК</b>\n\n"
            f"🔑 Твоих ключей: <b>{keys}</b>\n\n"
            "За один ключ можно получить редкий эмодзи.\n"
            "🎲 Шансы наград скрыты — удача решает всё!",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )

        bot.answer_callback_query(call.id)


    elif call.data == 'open_winter_chest':

        result = open_winter_chest(user_id)

        if not result:
            bot.answer_callback_query(
                call.id,
                "❌ У тебя нет снежинок!",
                show_alert=True
            )
            return

        if result["coins"]:

            reward_text = (
                f"{result['emoji']} <b>{result['rarity']}</b> эмодзи "
                "уже есть у тебя.\n"
                f"💰 Вместо дубликата: +{result['coins']} монет"
            )            
        else:

            reward_text = (
                f"🎁 Тебе выпало: <b>{result['emoji']}</b>\n"
                f"⭐ Редкость: <b>{result['rarity']}</b>\n"
                "🎒 Эмодзи добавлено в инвентарь!"
            )

        keys_left = get_snowflakes(user_id)

        markup = types.InlineKeyboardMarkup(row_width=1)

        markup.add(
            types.InlineKeyboardButton(
                "❄️ Открыть ещё",
                callback_data="open_winter_chest"
            )
        )

        markup.add(
            types.InlineKeyboardButton(
                "🔙 Назад к играм",
                callback_data="back_to_games"
            )
        )

        bot.edit_message_text(
            f"🕯️ <b>ЗИМНИЙ СУНДУК ОТКРЫТ!</b>\n\n"
            f"{reward_text}\n\n"
            f"🔑 Осталось ключей: <b>{keys_left}</b>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="HTML"
        )

        bot.answer_callback_query(call.id)
        
    # ===== ИГРА: БИЗНЕСМЕН (С POP-UP УВЕДОМЛЕНИЯМИ) =====
    elif call.data == 'start_clicker':
        set_active_game(user_id, call.message.chat.id, 'clicker')
        user = get_user(user_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💸 КЛИК!", callback_data="game_click"),
            types.InlineKeyboardButton("🏙 Недвижимость", callback_data="realestate"),
            types.InlineKeyboardButton("🛒 Магазин", callback_data="shop_business"),
            types.InlineKeyboardButton("🛑 Версия игры", callback_data='version_business')
        )
        bot.edit_message_text(
            f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {user[5]} монет 💰\nДля выхода введи /game_stop", 
            chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        return
    
    elif call.data == 'game_click':
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET coins = coins + 10 WHERE user_id = ?", (user_id,))
        conn.commit()
                    
        updated_user = get_user(user_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💸 КЛИК!", callback_data="game_click"),
            types.InlineKeyboardButton("🏙 Недвижимость", callback_data="realestate"),
            types.InlineKeyboardButton("🛒 Магазин", callback_data="shop_business"),
            types.InlineKeyboardButton("🛑 Версия игры", callback_data='version_business')
        )
        try:
            bot.edit_message_text(
                f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {updated_user[5]} монет 💰\nДля выхода введи /game_stop", 
                chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML"
            )
        except:
            pass
        bot.answer_callback_query(call.id, "💥 КЛИК! +10 💰", show_alert=False)
        
    elif call.data == 'settings_user':
        bot.edit_message_text(
            "⚙ **Настройки профиля:**\n\nВыберите нужный параметр:",
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=settings_menu(user_id),
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)

    elif call.data == 'toggle_kb':
        toggle_user_setting(user_id, 'kb_type')
        kb_type, _, _ = get_user_settings(user_id)
    
        if kb_type == 'reply':
           # 1. Если переключили на Reply:
           # Убираем Inline-клавиатуру у текущего сообщения и информируем
            bot.edit_message_text(
                chat_id=user_id,
                message_id=call.message.message_id,
                text="⌨ Вы переключились на **обычные кнопки** снизу!",
                parse_mode="Markdown"
            )
        # Отправляем Reply-клавиатуру новым сообщением
            bot.send_message(
                user_id,
                "Настройки обновлены:",
                reply_markup=settings_menu_reply(user_id)
            )
        else:
        # 2. Если переключили обратно на Inline:
        # Сначала убираем снизу Reply-клавиатуру
            bot.send_message(
                user_id,
                "⌨ Переключено на **Inline-кнопки**!",
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode="Markdown"
            )
        # Обновляем Inline-кнопки под исходным сообщением
            bot.edit_message_reply_markup(
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=settings_menu(user_id)
            )
        
        bot.answer_callback_query(call.id, "Вид кнопок изменен!")

    elif call.data == 'toggle_notif':
        toggle_user_setting(user_id, 'notifications')
        _, notifications, _ = get_user_settings(user_id)
        
        msg_text = "Уведомления включены 🔔" if notifications == 1 else "Уведомления отключены 🔕"
        bot.edit_message_reply_markup(chat_id=user_id, message_id=call.message.message_id, reply_markup=settings_menu(user_id))
        bot.answer_callback_query(call.id, msg_text)

    elif call.data == 'toggle_show_id':
        toggle_user_setting(user_id, 'show_id')
        _, _, show_id = get_user_settings(user_id)
        
        msg_text = "Ваш ID теперь виден другим 👁" if show_id == 1 else "Ваш ID скрыт от остальных 🙈"
        bot.answer_callback_query(call.id, "Примечание !\nОт тогдо что вы измените этот парамент ничего не изменится пока это парамент не доделан !", show_alert=True)
        bot.edit_message_reply_markup(chat_id=user_id, message_id=call.message.message_id, reply_markup=settings_menu(user_id))
        bot.answer_callback_query(call.id, msg_text)
        
    elif call.data == 'realestate':
        properties = get_user_properties(user_id)
        if properties:
            text = "🏙 <b>Твоя недвижимость:</b>\n\n"
            for prop_name, income in properties:
                text += f"▪️ {prop_name} (Доход: +{income}💵/мин)\n"
        else:
            text = "🏙 <b>Твоя недвижимость:</b>\n\nУ тебя пока нет купленной недвижимости!"
                        
        kup = types.InlineKeyboardMarkup(row_width=1)
        kup.add(types.InlineKeyboardButton("⬅ Назад к кликеру", callback_data="back_to_clicker"))
        
        bot.edit_message_text(text, chat_id=user_id, message_id=call.message.message_id, reply_markup=kup, parse_mode="HTML")
        
    elif call.data == 'back_to_clicker':
        user = get_user(user_id)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("💸 КЛИК!", callback_data="game_click"),
            types.InlineKeyboardButton("🏙 Недвижимость", callback_data="realestate"),
            types.InlineKeyboardButton("🛒 Магазин", callback_data="shop_business"),
            types.InlineKeyboardButton("🛑 Версия игры", callback_data='version_business')
        )
        bot.edit_message_text(
            f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {user[5]} монет 💰\nДля выхода введи /game_stop", 
            chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == 'version_business':
        bot.answer_callback_query(call.id, "⚙ Версия игры 1.0")
        
    elif call.data == 'shop_business':
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🏠 Купить Квартиру (500 💰)", callback_data="buy_flat"),
            types.InlineKeyboardButton("🏢 Купить Офис (2000 💰)", callback_data="buy_office"))
        markup.add(types.InlineKeyboardButton("⬅ Назад к кликеру", callback_data="back_to_clicker"))
        bot.edit_message_text(
            "🛒 <b>Магазин недвижимости</b>\n\nВыберите объект для покупки:", 
            chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
        
    elif call.data == "buy_flat":
        success = buy_property(user_id, "Квартира в центре", "flat", 500, 50)
        if success:
            bot.answer_callback_query(call.id, "🎉 Вы успешно купили Квартиру!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ Недостаточно монет для покупки!", show_alert=True)
            
    elif call.data == "buy_office":
        success = buy_property(user_id, "Бизнес-Офис", "office", 2000, 250)
        if success:
            bot.answer_callback_query(call.id, "🎉 Вы успешно купили Офис!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ Недостаточно монет для покупки!", show_alert=True)
    
    # ===== ИГРА: ЛЕТНЕЕ КОЛЕСО (С АНИМАЦИЕЙ ВРАЩЕНИЯ) =====
    elif call.data == 'start_wheel':
        set_active_game(user_id, call.message.chat.id, 'wheel')
        
        # Анимация спиннера
        frames = [
            "🎡 [ ⬛ 🟩 🟨 ] <i>Крутим колесо...</i>",
            "🎡 [ 🟨 ⬛ 🟩 ] <i>Ускоряемся...</i>",
            "🎡 [ 🟩 🟨 ⬛ ] <i>Почти остановилось...</i>"
        ]
        for frame in frames:
            try:
                bot.edit_message_text(frame, chat_id=user_id, message_id=call.message.message_id, parse_mode="HTML")
                time.sleep(0.4)
            except:
                pass
        
        cursor = conn.cursor()
        summer_events = [
            {"text": "🍦 Ты съел вкусное мороженое на пляже! Найдено в кармане: +20 💰", "type": "coins", "value": 20},
            {"text": "☀️ Отличный солнечный день! Получено +40 XP", "type": "XP", "value": 40},
            {"text": "🍋 Ты выпил лимонад! +15 💰", "type": "coins", "value": 15},
            {"text": "🚴 Катался на велике целый день! +60 XP", "type": "XP", "value": 60},
            {"text": "🥵 Получил солнечный удар! -15 💰", "type": "coins", "value": -15},
            {"text": "🍉 Купил арбуз и поделился! +30 💰", "type": "coins", "value": 30},
            {"text": "🦟 Искусали комары! -20 XP", "type": "XP", "value": -20}
        ]
        
        event = random.choice(summer_events)
        if event["type"] == "coins":
            cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (event["value"], user_id))
        elif event["type"] == "XP":
            cursor.execute("UPDATE users SET XP = XP + ? WHERE user_id = ?", (event["value"], user_id))
        conn.commit()
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton('🎡 Крутить еще раз', callback_data='start_wheel'),
            types.InlineKeyboardButton('🔙 В меню игр', callback_data='game')
        )
        bot.edit_message_text(
            f"🎡 <b>ЛЕТНЕЕ КОЛЕСО УДАЧИ</b>\n\n<blockquote>{event['text']}</blockquote>", 
            chat_id=user_id, 
            message_id=call.message.message_id, 
            reply_markup=markup,
            parse_mode="HTML"
        )
    
    # ===== ИГРА: УГАДАЙ ЧИСЛО =====
    elif call.data == 'start_number':
        secret = random.randint(1, 10)
        attempts = 5
        set_active_game(user_id, call.message.chat.id, 'number', secret_number=secret, attempts=attempts)
        
        bot.edit_message_text(
            f'*ИГРА: УГАДАЙ ЧИСЛО*\n\n'
            f'🔢 Я загадал число от 1 до 10\n'
            f'❤️ У тебя {attempts} попыток\n\n'
            f'Введи число:',
            chat_id=user_id, message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
        
    elif call.data == "settings_id":
        global_data = openbot.get_id(uid)
        
        if global_data:
            g_name = global_data[2]
            g_tag = global_data[5] or "Не установлен"
            g_bio = global_data[8] or "Не установлена"
            g_data = global_data[7]
            g_active_bot = openbot.get_active_bots(uid)

            num_status = openbot.get_user_level(user_id)
            raw_status = openbot.get_status(user_id)
            g_status = ROLE.get(raw_status, f"{raw_status}")

            if raw_status in ["developer", "owner", "admin", "tester"]:
                full_status_display = f"{g_status} [{num_status}]"
            else:
                full_status_display = g_status
            
            text = f"""**🌐 Общий профиль Openbot AI ID**

Вы можете изменить свои глобальные данные. Они обновятся во всех ботах нашей сети!

**🏷 Текущее имя:** `{g_name}`
**🆔 Ваш Тег:** `{g_tag}`
**🎭 Глобальный статус:** `{full_status_display}`
**🤖 Боты:** `{g_active_bot}`
**🗄 Создан аккаунт:** `{g_data}`
**📝 О себе:** `{g_bio}`
"""
        else:
            text = "❌ У вас еще не создан Openbot AI ID. Напишите /start для автоматической регистрации."

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton('⬅️ Назад в профиль', callback_data='profile')
        )
        
        bot.edit_message_text(text, cid, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    
    # ===== ИГРА: КУБИК СУДЬБЫ =====
    elif call.data == 'start_dice':
        secret = random.randint(1, 6)
        set_active_game(user_id, call.message.chat.id, 'dice', secret_number=secret, attempts=1)
        
        bot.edit_message_text(
            f'🎲 *КУБИК СУДЬБЫ*\n\n'
            f'🎲 Я загадал число от 1 до 6\n'
            f'⚡ Угадай и получи награду!\n\n'
            f'Введи число:',
            chat_id=user_id, message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
    
    # ===== ИГРА: ВЗЛОМ КОДА =====
    elif call.data == 'start_code':
        secret_code = ''.join([str(random.randint(0, 9)) for _ in range(4)])
        set_active_game(user_id, call.message.chat.id, 'code', attempts=8, secret_code=secret_code)
        
        bot.edit_message_text(
            f'🔐 *ВЗЛОМ КОДА*\n\n'
            f'🔐 Я загадал 4-значный код (0000-9999)\n'
            f'❤️ У тебя 8 попыток\n'
            f'💡 После каждой попытки получишь подсказку\n\n'
            f'Введи код:',
            chat_id=user_id, message_id=call.message.message_id,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
    
    # ===== ИГРА: ТОРГОВЕЦ НА БАЗАРЕ =====
    elif call.data == 'start_trader':
        set_active_game(user_id, call.message.chat.id, 'trader')
        
        goods = [
            {"name": "📱 Смартфон", "buy": 100, "sell": 180, "profit": 80},
            {"name": "👕 Рубашка", "buy": 20, "sell": 25, "profit": 5},
            {"name": "⌚ Часы", "buy": 150, "sell": 400, "profit": 250},
            {"name": "👟 Кроссовки", "buy": 60, "sell": 100, "profit": 40},
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for idx, good in enumerate(goods):
            markup.add(types.InlineKeyboardButton(
                f"{good['name']} (Куплено: {good['buy']}💰, Продать: {good['sell']}💰)",
                callback_data=f"trader_sell_{idx}"
            ))
        markup.add(types.InlineKeyboardButton("🔙 В меню игр", callback_data="game"))
        
        bot.edit_message_text(
            f'💰 *ТОРГОВЕЦ НА БАЗАРЕ*\n\n'
            f'Ты купил несколько товаров на базаре.\n'
            f'Выбери товар для продажи и получи прибыль!\n\n'
            f'(Выбери правильно и заработаешь максимум)',
            chat_id=user_id, message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
    
    elif call.data.startswith('trader_sell_'):
        goods = [
            {"name": "📱 Смартфон", "buy": 100, "sell": 180, "profit": 80},
            {"name": "👕 Рубашка", "buy": 20, "sell": 25, "profit": 5},
            {"name": "⌚ Часы", "buy": 150, "sell": 400, "profit": 250},
            {"name": "👟 Кроссовки", "buy": 60, "sell": 100, "profit": 40},
        ]
        
        good_index = int(call.data.split('_')[2])
        selected_good = goods[good_index]
        profit = selected_good['profit']
        
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (profit, user_id))
        conn.commit()
        
        delete_active_game(user_id)
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton('💰 Торговать снова', callback_data='start_trader'),
            types.InlineKeyboardButton('🔙 В меню игр', callback_data='game')
        )
        
        bot.edit_message_text(
            f'💰 *ТОРГОВЕЦ НА БАЗАРЕ*\n\n'
            f'✅ Ты продал: {selected_good["name"]}\n'
            f'💰 Прибыль: +{profit} монет!\n\n'
            f'Спасибо за сделку! 🤝',
            chat_id=user_id, message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id, f"Прибыль: +{profit} 💰")
    # ===== Новый игры и обнова =====
    if call.data == 'back_to_games':
        _mnew_clear(user_id)
        delete_active_game(user_id)
        bot.edit_message_text(_master_games_menu_text(), chat_id=cid, message_id=mid,
                              reply_markup=game_mune(user_id), parse_mode='HTML')
        bot.answer_callback_query(call.id)
        return

    # 1. Тайна снежного шара
    elif call.data == 'game_guess_num':
        secret = random.randint(1, 10)
        set_active_game(user_id, cid, 'witch_guess', secret_number=secret, attempts=5)
        bot.edit_message_text(
            "🔮 <b>Тайна снежного шара</b>\n\nСнежный шар загадал число от <b>1</b> до <b>10</b>.\n"
            "У тебя есть <b>5 попыток</b>!\n\n💬 <i>Напиши число в чат:</i>",
            chat_id=cid, message_id=mid, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)

    # Крестики-нолики из Random Bot: уровни сложности, проверка ходов, награды.
    elif call.data == 'game_ttt':
        delete_active_game(user_id)
        bot.edit_message_text('❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\n\nВыбери сложность ИИ:',
                              chat_id=cid, message_id=mid, reply_markup=ttt_level_markup(), parse_mode='HTML')
        bot.answer_callback_query(call.id)

    elif call.data.startswith('ttt_level_'):
        level = call.data[len('ttt_level_'):]
        if level not in _TTT_LEVELS:
            return bot.answer_callback_query(call.id, 'Неизвестная сложность', show_alert=True)
        ttt_start(user_id, cid, level)
        bot.edit_message_text(
            f'❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\nИИ: {_TTT_LEVELS[level]}\n\nТвой ход: ❌',
            chat_id=cid, message_id=mid,
            reply_markup=build_ttt_keyboard('0' * 9), parse_mode='HTML')
        bot.answer_callback_query(call.id)

    elif call.data == 'ttt_done':
        bot.answer_callback_query(call.id, 'Партия завершена. Нажми «Играть снова».')

    elif call.data.startswith('ttt_pos_'):
        try:
            pos = int(call.data.rsplit('_', 1)[1])
        except (IndexError, ValueError):
            return bot.answer_callback_query(call.id, 'Некорректный ход', show_alert=True)
        result, board, notice = ttt_play(user_id, pos)
        if result == 'invalid':
            return bot.answer_callback_query(call.id, notice[:190], show_alert=True)
        markup = build_ttt_keyboard(board, finished=(result == 'done'))
        if result == 'done':
            markup.add(types.InlineKeyboardButton('🔄 Играть снова', callback_data='game_ttt'),
                       types.InlineKeyboardButton('⬅ Игры', callback_data='game'))
        bot.edit_message_text(notice, chat_id=cid, message_id=mid,
                              reply_markup=markup, parse_mode='HTML')
        bot.answer_callback_query(call.id)

    # 3. Битва со снежным големом
    elif call.data == 'game_duel':
        set_active_game(user_id, cid, 'duel', secret_number=100, attempts=100)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("⚔️ Атака", callback_data="duel_act_attack"),
            types.InlineKeyboardButton("🛡️ Щит", callback_data="duel_act_defend"),
            types.InlineKeyboardButton("🧪 Зелье", callback_data="duel_act_heal"),
            types.InlineKeyboardButton("🏃 Сбежать", callback_data="back_to_games")
        )
        bot.edit_message_text("⚰️ <b>Битва со снежным големом</b>\n\n❤️ Твое HP: 100 | ⛄ Голем: 100", chat_id=cid, message_id=mid, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif call.data.startswith('duel_act_'):
        game_data = get_active_game(user_id)
        if not game_data or game_data[0] != 'duel':
            bot.answer_callback_query(call.id, "Битва окончена!", show_alert=True)
            return
        action = call.data.split('_')[2]
        ghost_hp, player_hp = game_data[1], game_data[2]
        text_log = ""
        if action == "attack":
            dmg = random.randint(20, 35)
            ghost_hp -= dmg
            text_log += f"⚔️ Урон снежному голему: <b>{dmg}</b>\n"
        elif action == "defend":
            text_log += "🛡️ Ты встал в блок!\n"
        elif action == "heal":
            heal = random.randint(15, 30)
            player_hp = min(100, player_hp + heal)
            text_log += f"🧪 Восстановлено <b>{heal} HP</b>!\n"

        if ghost_hp <= 0:
            add_coins(user_id, 75)
            add_xp(user_id, 50)
            try_drop_snowflake(user_id, cid)
            delete_active_game(user_id)
            finish_inline_game(call, "🏆 <b>Победа!</b> Ты изгнал Голема!\n💰 +75 монет, +50 XP")
            return

        ghost_dmg = random.randint(10, 25) // (2 if action == "defend" else 1)
        player_hp -= ghost_dmg
        text_log += f"⛄ Голем нанес <b>{ghost_dmg}</b> урона!"

        if player_hp <= 0:
            delete_active_game(user_id)
            finish_inline_game(call, "❄️ <b>Ты пал в бою...</b>")
            return

        update_game_state(user_id, secret_number=ghost_hp, attempts=player_hp)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("⚔️ Атака", callback_data="duel_act_attack"),
            types.InlineKeyboardButton("🛡️ Щит", callback_data="duel_act_defend"),
            types.InlineKeyboardButton("🧪 Зелье", callback_data="duel_act_heal"),
            types.InlineKeyboardButton("🏃 Сбежать", callback_data="back_to_games")
        )
        bot.edit_message_text(f"⚰️ <b>Битва со снежным големом</b>\n\n{text_log}\n\n❤️ HP: {player_hp} | ⛄ Голем: {ghost_hp}", chat_id=cid, message_id=mid, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    # 4. Подарок или сюрприз
    elif call.data == 'game_trick_or_treat':
        markup = types.InlineKeyboardMarkup(row_width=3)
        markup.add(
            types.InlineKeyboardButton("🎁 #1", callback_data="tot_pick_1"),
            types.InlineKeyboardButton("🎁 #2", callback_data="tot_pick_2"),
            types.InlineKeyboardButton("🎁 #3", callback_data="tot_pick_3")
        )
        markup.add(types.InlineKeyboardButton("🔙 В меню", callback_data="back_to_games"))
        bot.edit_message_text("🍬 Выбери подарок!", chat_id=cid, message_id=mid, reply_markup=markup)
        bot.answer_callback_query(call.id)

    elif call.data.startswith('tot_pick_'):
        outcomes = [
            ("🎁 <b>Подарок!</b> Тебе повезло!", 35, 60),
            ("⛄ <b>Пакость!</b> Летучая мышь утащила монеты!", -15, 30),
            ("🎆 <b>Джекпот!</b> Золотой подарок!", 60, 10)
        ]

        res = random.choices(
            outcomes,
            weights=[60, 30, 10],
            k=1
        )[0]

        add_coins(user_id, res[1])

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(
                "🔄 Еще раз",
                callback_data="game_trick_or_treat"
            ),
            types.InlineKeyboardButton(
                "🔙 В меню",
                callback_data="back_to_games"
            )
        )

        bot.edit_message_text(
            f"{res[0]}\n"
            f"Результат: <b>{res[1]:+} монет</b>",
            chat_id=cid,
            message_id=mid,
            reply_markup=markup,
            parse_mode="HTML"
        )

        bot.answer_callback_query(call.id)
        
    # 5. Поиск зимних сокровищ
    elif call.data == 'game_graveyard':
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔎 Искать", callback_data="graveyard_dig"),
            types.InlineKeyboardButton("🔙 В меню", callback_data="back_to_games")
        )
        bot.edit_message_text("🔎 <b>Поиск зимних сокровищ</b>", chat_id=cid, message_id=mid, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

    elif call.data == 'graveyard_dig':
        loot_type = random.choices(['coins', 'item', 'nothing'], weights=[60, 25, 15])[0]
        if loot_type == 'coins':
            found = random.randint(15, 50)
            add_coins(user_id, found)
            msg = f"💰 Нашёл <b>+{found} монет</b>!"
        elif loot_type == 'item':
            item = random.choice(['❄️', '🦴', '🔮', '📜'])
            add_to_inventory(user_id, item)
            msg = f"✨ Нашёл артефакт <b>{item}</b>!"
        else:
            msg = "🧹 Ничего не нашёл..."

        # 3% шанс найти снежинка во время раскопок
        try_drop_snowflake(user_id, call.id)

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔎 Искать ещё", callback_data="graveyard_dig"),
            types.InlineKeyboardButton("🔙 В меню", callback_data="back_to_games")
        )
        bot.edit_message_text(f"🔎 <b>Поиск сокровищ</b>\n\n{msg}", chat_id=cid, message_id=mid, reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)

# ============ СОСТОЯНИЕ REPLY-МЕНЮ ============
# Отдельное состояние Reply-клавиатур для каждого пользователя.
reply_case_page = {}
reply_inventory_page = {}
reply_market_page = {}
reply_selected_emoji = {}

@bot.message_handler(func=lambda message: (
    bool(message.text) and (
        message.text in {
            "❄️ Зимние игры", "👤 Профиль", "🎁 Зимняя лавка", "🎒 Инвентарь",
            "🏪 Рынок", "❄️ Зимний сундук", "⚙️ Настройки", "⚙ Настройки",
            "⬅ Главное меню", "⬅ В главное меню",
            "🎁 Кейсы", "⭐ Магазин Stars", "⬅ Магазин",
            "👑 Титулы за монеты", "❄️ Титулы за снежинки", "☃️ Обмен снежков",
            "💰 10 000 000 монет — 100 ⭐", "❄️ 25 снежинок — 50 ⭐",
            "⭐ Прокачать до 20 уровня — 150 ⭐",
            "❄️ Открыть Зимний сундук",
            "⬅ Инвентарь", "◀️ Предыдущая страница", "Следующая страница ▶️",
            "📦 Мои лоты", "⬅ Рынок",
            "◀️ Предыдущая страница рынка", "Следующая страница рынка ▶️",
            "⬅ Предыдущие кейсы", "Следующие кейсы ➡",
            "🔮 Тайна снежного шара", "❌⭕ Крестики-нолики", "❄️ Снежинки vs ⛄ Снеговики",
            "⛄ Битва со снежным големом", "🎁 Подарок или сюрприз", "🔎 Поиск зимних сокровищ",
            "🧊 Ледяной лабиринт", "🎄 Мастерская гирлянд", "🐧 Экспедиция пингвина", "🔔 Морозный шифр",
            "⌨ Переключить вид кнопок: Inline", "⌨ Переключить вид кнопок: Reply",
            "🔔 Уведомления: ВКЛ", "🔕 Уведомления: ВЫКЛ",
            "👁 ID для других: Виден", "🙈 ID для других: Скрыт",
            "⚙ Настройки ID", "📅 Календарь декабря", "🏆 Мои титулы", "📊 Лидеры",
        }
        or message.text.startswith((
            "🛒 ", "🔓 Открыть ", "👕 ", "💰 Продать ", "🏪 Выставить ",
            "❌ Снять лот #", "🛒 Купить ", "🏆 ", "🔒 ", "🧪 ",
        )) or bool(INV_BTN_RE.match(message.text)) or message.text in MASTER_GAME_BUTTONS.values() or message.text in MASTER_UPCOMING_GAMES.values()
    )
))
def handle_all_reply_menus(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    text = message.text or ""
    # Обе клавиатуры читают одну и ту же сохранённую причину.
    chosen = next((key for key, (name, _) in _master_feature_catalog().items()
                   if text in (name, MASTER_GAME_BUTTONS.get(key),
                               '🔒 ' + name, '🔒 ' + MASTER_GAME_BUTTONS.get(key, name),
                               '🧪 ' + name, '🧪 ' + MASTER_GAME_BUTTONS.get(key, name))), None)
    if chosen and not _master_can_use_game(chosen, uid):
        return bot.send_message(chat_id, _master_feature_closed_text(chosen),
                                reply_markup=game_mune_reply(uid))
    if chosen == 'ttt':
        return bot.send_message(
            chat_id, '❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\n\nВыбери сложность:',
            parse_mode='HTML', reply_markup=ttt_level_markup(reply=True))
    if chosen in set(_master_feature_catalog()) - {'ttt'}:
        return _mnew_start(uid, chat_id, chosen)

    if text in {'🔮 Тайна снежного шара','⛄ Битва со снежным големом',
                '🎁 Подарок или сюрприз','🔎 Поиск зимних сокровищ'}:
        return bot.send_message(chat_id,'🔒 Эта игра заменена новой. Открой меню зимних игр.')
    game_labels={v[0]:k for k,v in MASTER_FEATURES.items()}
    blocked_feature=game_labels.get(text)
    if blocked_feature and not _master_can_use_game(blocked_feature, uid):
        bot.send_message(chat_id,'🔒 Игра временно отключена.')
        return

    if text == '📅 Календарь декабря':return master_calendar_command(message)
    if text == '🏆 Мои титулы':return master_my_titles_command(message)
    if text == '📊 Лидеры':return master_leaders_command(message)

    # Главное меню
    if text in ("⬅ Главное меню", "⬅ В главное меню"):
        delete_active_game(uid)
        pending_market.pop(uid, None)
        reply_selected_emoji.pop(uid, None)
        send_main_reply_menu(chat_id, message.from_user.first_name)
        return

    if text == "❄️ Зимние игры":
        delete_active_game(uid)
        bot.send_message(chat_id, _master_games_menu_text(),
                         reply_markup=game_mune_reply(uid), parse_mode='HTML')
        return

    if text == "👤 Профиль":
        delete_active_game(uid)
        bot.send_message(chat_id, get_profile_text(uid), reply_markup=profile_menu_reply(), parse_mode="HTML")
        return
    
    if text == "⚙ Настройки ID":
        try:
            global_data = openbot.get_id(uid)
        except Exception as e:
            print(f"[REPLY SETTINGS ID] {e}")
            global_data = None

        if global_data:
            g_name = global_data[2] or "Не установлено"
            g_tag = global_data[5] or "Не установлен"
            g_bio = global_data[8] or "Не установлена"

            try:
                raw_status = openbot.get_status(uid)
                g_status = ROLE.get(
                    raw_status,
                    str(raw_status)
                )
            except Exception:
                g_status = "👤 Игрок"
    
            try:
                g_level = openbot.get_user_level(uid)
            except Exception:
                g_level = 1

            try:
                g_active_bot = openbot.get_active_bots(uid)
            except Exception:
                g_active_bot = "Неизвестно"

            text_out = (
                "🌐 <b>OPENBOT ID</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 <b>Имя:</b> {html.escape(str(g_name))}\n"
                f"🆔 <b>Тег:</b> <code>{html.escape(str(g_tag))}</code>\n"
                f"🎭 <b>Статус:</b> {html.escape(str(g_status))}\n"
                f"📊 <b>Уровень ID:</b> {g_level}\n"
                f"🤖 <b>Активных ботов:</b> {html.escape(str(g_active_bot))}\n"
                f"📝 <b>О себе:</b> {html.escape(str(g_bio))}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🌍 <i>Это твой глобальный OpenBot ID.</i>"
            )    

        else:
            text_out = (
                "🌐 <b>OPENBOT AI ID</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "❌ <b>OpenBot ID не найден.</b>\n\n"
                "Для создания ID используй /start."
            )

        markup = types.ReplyKeyboardMarkup(
            row_width=2,
            resize_keyboard=True
        )    

        markup.add(
            types.KeyboardButton("⬅ Главное меню")
        )

        bot.send_message(
            chat_id,
            text_out,
            parse_mode="HTML",
            reply_markup=markup
        )
        return

    if text == "🎒 Инвентарь":
        reply_inventory_page[uid] = 1
        bot.send_message(chat_id, inventory_reply_text(uid, 1), reply_markup=inventory_menu_reply(uid, 1), parse_mode="HTML")
        return

    # Настройки (только для обычных настроек, не для OpenBot ID)
    if text in ("⚙️ Настройки", "⚙ Настройки"):
        bot.send_message(chat_id, "⚙️ <b>НАСТРОЙКИ</b>\n━━━━━━━━━━━━━━━━━━\n\nВыбери параметр:", reply_markup=settings_menu_reply(uid), parse_mode="HTML")
        return

    if text in ("🔔 Уведомления: ВКЛ", "🔕 Уведомления: ВЫКЛ"):
        toggle_user_setting(uid, "notifications")
        _, notifications, _ = get_user_settings(uid)
        msg = "🔔 Уведомления включены" if notifications else "🔕 Уведомления отключены"
        bot.send_message(chat_id, msg, reply_markup=settings_menu_reply(uid))
        return

    if text in ("👁 ID для других: Виден", "🙈 ID для других: Скрыт"):
        toggle_user_setting(uid, "show_id")
        _, _, show_id = get_user_settings(uid)
        msg = "👁 ID теперь виден другим" if show_id else "🙈 ID теперь скрыт от других"
        bot.send_message(chat_id, "Примечание !\nОт тогдо что вы измените этот парамент ничего не изменится пока это парамент не доделан !")
        bot.send_message(chat_id, msg, reply_markup=settings_menu_reply(uid))
        return

    if text in ("⌨ Переключить вид кнопок: Inline", "⌨ Переключить вид кнопок: Reply"):
        new_type = toggle_user_setting(uid, "kb_type")
        bot.send_message(chat_id, "⚙️ Настройки обновлены", reply_markup=types.ReplyKeyboardRemove())
        if new_type == "reply":
            send_main_reply_menu(chat_id, message.from_user.first_name, "⌨️ <b>Обычные кнопки включены.</b>\n\n👇 Главное меню:")
        else:
            bot.send_message(chat_id, "⌨️ <b>Inline-кнопки включены.</b>\n\n👇 Главное меню:", reply_markup=main_mune(), parse_mode="HTML")
        return

    # Магазин
    if text == "🎁 Зимняя лавка":
        bot.send_message(chat_id, _winter_shop_front(uid), reply_markup=shop_mune_reply(), parse_mode="HTML")
        return

    if text == "⬅ Магазин":
        bot.send_message(chat_id, _winter_shop_front(uid), reply_markup=shop_mune_reply(), parse_mode="HTML")
        return

    if text == "🎁 Кейсы":
        reply_case_page[uid] = 0
        bot.send_message(chat_id, cases_text(uid, 0), reply_markup=cases_menu_reply(uid, 0), parse_mode="HTML")
        return

    if text == "⭐ Магазин Stars":
        bot.send_message(chat_id, "⭐ <b>МАГАЗИН STARS</b>\n━━━━━━━━━━━━━━━━━━\n\nВыбери покупку:", reply_markup=stars_shop_menu_reply(), parse_mode="HTML")
        return

    stars_map = {
        "💰 10 000 000 монет — 100 ⭐": "coins_10m",
        "❄️ 25 снежинок — 50 ⭐": "snowflakes_25",
        "⭐ Прокачать до 20 уровня — 150 ⭐": "level_20",
    }
    if text in stars_map:
        send_stars_invoice_from_message(message, stars_map[text])
        return

    # Титулы
    if text in {"🏆 Титулы", "👑 Титулы за монеты"}:
        bot.send_message(chat_id, winter_titles_text(uid),
                         reply_markup=winter_titles_menu(0, uid), parse_mode='HTML')
        return
    if text == "❄️ Титулы за снежинки":
        caption, inline_kb = _master_snow_menu(uid)
        bot.send_message(chat_id, caption, reply_markup=inline_kb, parse_mode='HTML')
        return
    if text == "☃️ Обмен снежков":
        caption, inline_kb = _snowballs_exchange_menu(uid)
        bot.send_message(chat_id, caption, reply_markup=inline_kb, parse_mode='HTML')
        return

    if text.startswith("🏆 ") and " — " in text and text.endswith(" 🪙"):
        selected = text[2:].rsplit(" — ", 1)[0]
        title_key = next((k for k, (name, _) in WINTER_TITLES.items() if name == selected), None)
        if title_key:
            _, msg = buy_winter_title(uid, title_key)
            page = list(WINTER_TITLES).index(title_key) // TITLE_CATALOG_PAGE_SIZE
            bot.send_message(chat_id, winter_titles_text(uid, msg, page=page),
                             reply_markup=winter_titles_menu(page, uid), parse_mode='HTML')
            return

    # Сундук
    if text == "❄️ Зимний сундук":
        keys = get_snowflakes(uid)
        bot.send_message(chat_id, f"🕯️ <b>ЗИМНИЙ СУНДУК</b>\n━━━━━━━━━━━━━━━━━━\n\n❄️ Ключей: <b>{keys}</b>\n\nОдин ключ открывает сундук и выдаёт случайный приз.", reply_markup=winter_chest_reply(), parse_mode="HTML")
        return

    if text == "❄️ Открыть Зимний сундук":
        result = open_winter_chest(uid)
        if result is None:
            bot.send_message(chat_id, "❌ У тебя нет снежинок.", reply_markup=winter_chest_reply())
            return
        coins = result.get("coins", 0)
        extra = f"\n💰 Компенсация: +{coins} монет" if coins else ""
        bot.send_message(chat_id, f"🎁 <b>СУНДУК ОТКРЫТ!</b>\n━━━━━━━━━━━━━━━━━━\n\n{result['emoji']} {get_rarity_icon(result['rarity'])} <b>{result['rarity']}</b>{extra}\n\n❄️ Осталось: <b>{get_snowflakes(uid)}</b>", reply_markup=winter_chest_reply(), parse_mode="HTML")
        return

    # Инвентарь
    if text == "⬅ Инвентарь":
        reply_inventory_page[uid] = 1
        bot.send_message(chat_id, inventory_reply_text(uid, 1), reply_markup=inventory_menu_reply(uid, 1), parse_mode="HTML")
        return

    if text == "◀️ Предыдущая страница":
        page = min(max(1, reply_inventory_page.get(uid, 1) - 1), _inv_total_pages(uid))
        reply_inventory_page[uid] = page
        bot.send_message(chat_id, inventory_reply_text(uid, page), reply_markup=inventory_menu_reply(uid, page), parse_mode="HTML")
        return

    if text == "Следующая страница ▶️":
        page = min(reply_inventory_page.get(uid, 1) + 1, _inv_total_pages(uid))
        reply_inventory_page[uid] = page
        bot.send_message(chat_id, inventory_reply_text(uid, page), reply_markup=inventory_menu_reply(uid, page), parse_mode="HTML")
        return

    inv_match = INV_BTN_RE.match(text)
    if inv_match:
        emoji = inv_match.group(1)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1", (uid, emoji))
        if not cursor.fetchone():
            bot.send_message(chat_id, "❌ Такого эмодзи у тебя нет.\n\n" + inventory_reply_text(uid, reply_inventory_page.get(uid, 1)), reply_markup=inventory_menu_reply(uid, reply_inventory_page.get(uid, 1)), parse_mode="HTML")
            return
        reply_selected_emoji[uid] = emoji
        card_text, markup = inventory_item_reply(uid, emoji)
        bot.send_message(chat_id, card_text, reply_markup=markup, parse_mode="HTML")
        return

    if text.startswith("👕 Одеть") or text.startswith("👕 Снять"):
        emoji = reply_selected_emoji.get(uid)
        if not emoji:
            bot.send_message(chat_id, "❌ Сначала выбери эмодзи в инвентаре.", reply_markup=inventory_menu_reply(uid, reply_inventory_page.get(uid, 1)))
            return
        cursor = conn.cursor()
        if text.startswith("👕 Одеть"):
            cursor.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1", (uid, emoji))
            if not cursor.fetchone():
                bot.send_message(chat_id, "❌ Эмодзи больше нет в инвентаре.")
                return
            cursor.execute("UPDATE users SET equipped_item=? WHERE user_id=?", (emoji, uid))
            conn.commit()
            _, markup = inventory_item_reply(uid, emoji)
            bot.send_message(chat_id, f"👕 {emoji} теперь надето!", reply_markup=markup)
        else:
            cursor.execute("UPDATE users SET equipped_item='' WHERE user_id=? AND equipped_item=?", (uid, emoji))
            conn.commit()
            _, markup = inventory_item_reply(uid, emoji)
            bot.send_message(chat_id, f"👕 {emoji} снято.", reply_markup=markup)
        return

    if text.startswith("💰 Продать "):
        emoji = text[len("💰 Продать "):]
        price = get_emoji_price(emoji)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1", (uid, emoji))
        row = cursor.fetchone()
        if not row:
            bot.send_message(chat_id, "❌ Эмодзи не найдено.")
            return
        cursor.execute("DELETE FROM inventory WHERE id=?", (row[0],))
        cursor.execute("SELECT COUNT(*) FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=?", (uid, emoji))
        if cursor.fetchone()[0] == 0:
            cursor.execute("UPDATE users SET equipped_item='' WHERE user_id=? AND equipped_item=?", (uid, emoji))
        cursor.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (price, uid))
        conn.commit()
        reply_selected_emoji.pop(uid, None)
        bot.send_message(chat_id, f"💰 {emoji} продано за <b>{price:,}</b> 🪙.".replace(",", " ") + "\n\n" + inventory_reply_text(uid, reply_inventory_page.get(uid, 1)), reply_markup=inventory_menu_reply(uid, reply_inventory_page.get(uid, 1)), parse_mode="HTML")
        return

    if text.startswith("🏪 Выставить "):
        emoji = text[len("🏪 Выставить "):].removesuffix(" на рынок")
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1", (uid, emoji))
        if not cursor.fetchone():
            bot.send_message(chat_id, "❌ Эмодзи не найдено.")
            return
        pending_market[uid] = emoji
        bot.send_message(chat_id, f"🏪 <b>Выставление {emoji}</b>\n\nВведи цену в монетах, например <code>1500</code>.", parse_mode="HTML")
        return

    # Рынок
    if text == "🏪 Рынок":
        reply_market_page[uid] = 1
        market_text, markup = market_menu_reply(uid, 1)
        bot.send_message(chat_id, market_text, reply_markup=markup, parse_mode="HTML")
        return

    if text == "◀️ Предыдущая страница рынка":
        page = max(1, reply_market_page.get(uid, 1) - 1)
        reply_market_page[uid] = page
        market_text, markup = market_menu_reply(uid, page)
        bot.send_message(chat_id, market_text, reply_markup=markup, parse_mode="HTML")
        return

    if text == "Следующая страница рынка ▶️":
        page = reply_market_page.get(uid, 1) + 1
        reply_market_page[uid] = page
        market_text, markup = market_menu_reply(uid, page)
        bot.send_message(chat_id, market_text, reply_markup=markup, parse_mode="HTML")
        return

    if text == "📦 Мои лоты":
        market_text, markup = my_market_reply(uid)
        bot.send_message(chat_id, market_text, reply_markup=markup, parse_mode="HTML")
        return

    if text == "⬅ Рынок":
        market_text, markup = market_menu_reply(uid, reply_market_page.get(uid, 1))
        bot.send_message(chat_id, market_text, reply_markup=markup, parse_mode="HTML")
        return

    if text.startswith("🛒 Купить "):
        import re
        m = re.search(r"#(\d+)$", text)
        if not m:
            bot.send_message(chat_id, "❌ Не удалось определить лот.")
            return
        lot_id = int(m.group(1))
        cursor = conn.cursor()
        cursor.execute("SELECT seller_id, item_type, item_value, price FROM auction WHERE lot_id=?", (lot_id,))
        lot = cursor.fetchone()
        if not lot:
            bot.send_message(chat_id, "❌ Лот уже продан или снят.")
            return
        seller_id, item_type, emoji, price = lot
        if seller_id == uid:
            bot.send_message(chat_id, "❌ Нельзя купить собственный лот.")
            return
        if item_type != "emoji":
            bot.send_message(chat_id, "❌ Этот тип товара не поддерживается.")
            return
        cursor.execute("SELECT coins FROM users WHERE user_id=?", (uid,))
        row = cursor.fetchone()
        coins = int((row or [0])[0])
        if coins < price:
            bot.send_message(chat_id, f"❌ Недостаточно монет. Нужно <b>{price:,}</b> 🪙.".replace(",", " "), parse_mode="HTML")
            return
        cursor.execute("DELETE FROM auction WHERE lot_id=? AND seller_id=? AND price=?", (lot_id, seller_id, price))
        if cursor.rowcount != 1:
            conn.rollback()
            bot.send_message(chat_id, "❌ Лот уже купили.")
            return
        cursor.execute("UPDATE users SET coins=coins-? WHERE user_id=? AND coins>=?", (price, uid, price))
        if cursor.rowcount != 1:
            conn.rollback()
            bot.send_message(chat_id, "❌ Недостаточно монет.")
            return
        cursor.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (price, seller_id))
        cursor.execute("INSERT INTO inventory (user_id,item_type,item_value,is_active) VALUES (?,'emoji',?,0)", (uid, emoji))
        conn.commit()
        notify_market_sale(seller_id, uid, emoji, price)
        market_text, markup = market_menu_reply(uid, reply_market_page.get(uid, 1))
        bot.send_message(chat_id, f"🎉 Куплено {emoji} за <b>{price:,}</b> 🪙!".replace(",", " "), reply_markup=markup, parse_mode="HTML")
        return

    if text.startswith("❌ Снять лот #"):
        import re
        m = re.match(r"❌ Снять лот #(\d+)\s*(.*)$", text)
        if not m:
            bot.send_message(chat_id, "❌ Не удалось определить лот.")
            return
        lot_id = int(m.group(1))
        cursor = conn.cursor()
        cursor.execute("SELECT item_value FROM auction WHERE lot_id=? AND seller_id=? AND item_type='emoji'", (lot_id, uid))
        lot = cursor.fetchone()
        if not lot:
            bot.send_message(chat_id, "❌ Лот не найден.")
            return
        emoji = lot[0]
        cursor.execute("DELETE FROM auction WHERE lot_id=? AND seller_id=?", (lot_id, uid))
        if cursor.rowcount != 1:
            conn.rollback()
            bot.send_message(chat_id, "❌ Лот уже снят.")
            return
        cursor.execute("INSERT INTO inventory (user_id,item_type,item_value,is_active) VALUES (?,'emoji',?,0)", (uid, emoji))
        conn.commit()
        market_text, markup = my_market_reply(uid)
        bot.send_message(chat_id, f"↩️ {emoji} возвращён в инвентарь.", reply_markup=markup)
        return

    # Кейсы
    for case_key, c_info in CASES.items():
        if text.startswith(f"🛒 {c_info['name']} —"):
            ok, msg = buy_case(uid, case_key)
            bot.send_message(chat_id, f"{msg}\n\n{balance_line(uid)}", reply_markup=cases_menu_reply(uid, reply_case_page.get(uid, 0)), parse_mode="HTML")
            return
        if text.startswith(f"🔓 Открыть {c_info['name']} ("):
            result, msg = open_case(uid, case_key)
            if result is None:
                bot.send_message(chat_id, f"{msg}\n\n{balance_line(uid)}", reply_markup=cases_menu_reply(uid, reply_case_page.get(uid, 0)), parse_mode="HTML")
            else:
                bot.send_message(chat_id, f"🎁 <b>КЕЙС ОТКРЫТ!</b>\n\n{c_info['name']}\n\n✨ {msg}\n\n📦 Осталось: <b>{get_case_count(uid, case_key)}</b>\n\n{balance_line(uid)}", reply_markup=cases_menu_reply(uid, reply_case_page.get(uid, 0)), parse_mode="HTML")
            return

    if text in ("⬅ Предыдущие кейсы", "Следующие кейсы ➡"):
        current = reply_case_page.get(uid, 0)
        current += -1 if text == "⬅ Предыдущие кейсы" else 1
        _, page, _ = get_cases_page(current)
        reply_case_page[uid] = page
        bot.send_message(chat_id, cases_text(uid, page), reply_markup=cases_menu_reply(uid, page), parse_mode="HTML")
        return

    # Запуск крестиков-ноликов; новые игры обрабатываются выше.
    if text in {'❌⭕ Крестики-нолики','❄️ Снежинки vs ⛄ Снеговики'}:
        if not _master_can_use_game('ttt', uid):
            return bot.send_message(chat_id,_master_feature_closed_text('ttt'),reply_markup=game_mune_reply(uid))
        return bot.send_message(chat_id,'❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\n\nВыбери сложность:',
                                parse_mode='HTML',reply_markup=ttt_level_markup(reply=True))

@bot.message_handler(func=lambda m: (m.text or '') in {f'❌⭕ {title}' for title in _TTT_LEVELS.values()})
def ttt_reply_level(message):
    if _reply_reject_message(message):
        return
    level = next(k for k, title in _TTT_LEVELS.items() if message.text == f'❌⭕ {title}')
    uid = message.from_user.id
    if not _master_can_use_game('ttt', uid):
        return bot.send_message(message.chat.id, _master_feature_closed_text('ttt'))
    ttt_start(uid, message.chat.id, level)
    bot.send_message(message.chat.id,
                     f'❌⭕ <b>КРЕСТИКИ-НОЛИКИ</b>\nИИ: {_TTT_LEVELS[level]}\n\n'
                     + ttt_board_text('0' * 9) + '\n\nВыбери клетку 1–9:',
                     parse_mode='HTML', reply_markup=game_action_reply('ttt'))

@bot.message_handler(func=lambda m: (m.text or '').strip() in {str(n) for n in range(1,10)}
                     and (get_active_game(m.from_user.id) or (None,))[0] == 'ttt')
def ttt_reply_number(message):
    return handle_reply_game_actions(message)

# ============ REPLY-ДЕЙСТВИЯ ИГР ============
@bot.message_handler(func=lambda message: message.text in {
    "🛑 Выйти из игры", "⚔️ Атака", "🛡️ Щит", "🧪 Зелье", "🏃 Сбежать",
    "🔎 Искать", "🎁 Подарок 1", "🎁 Подарок 2", "🎁 Подарок 3",
    "🎄 1", "🎄 2", "🎄 3", "🎄 4", "🎄 5", "🎄 6", "🎄 7", "🎄 8", "🎄 9",
    "⬅ Игры"
})
def handle_reply_game_actions(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    text = message.text
    if text in {'⚔️ Атака','🛡️ Щит','🧪 Зелье','🏃 Сбежать','🔎 Искать',
                '🎁 Подарок 1','🎁 Подарок 2','🎁 Подарок 3'}:
        return bot.send_message(chat_id,'🔒 Старая игра закрыта. Выбери новую в меню.',reply_markup=game_mune_reply(uid))
    game = get_active_game(uid)

    if text == "⬅ Игры":
        delete_active_game(uid)
        bot.send_message(chat_id, _master_games_menu_text(),
                         reply_markup=game_mune_reply(uid), parse_mode='HTML')
        return

    if text == "🛑 Выйти из игры" or text == "🏃 Сбежать":
        delete_active_game(uid)
        bot.send_message(chat_id, "🏃 Игра завершена.", reply_markup=game_mune_reply(uid))
        return

    if not game:
        bot.send_message(chat_id, "ℹ️ Активной игры нет.", reply_markup=game_mune_reply(uid))
        return

    game_type = game[0]

    # --- ❌⭕ Крестики-нолики ---
    if game_type == "ttt" and (text.strip() in {str(i) for i in range(1, 10)} or text.startswith("🎄 ")):
        if not _master_can_use_game('ttt', uid):
            return bot.send_message(chat_id, _master_feature_closed_text('ttt'), reply_markup=game_mune_reply(uid))
        try:
            pos = int(text.split()[-1]) - 1
        except (ValueError, IndexError):
            return bot.send_message(chat_id, '❌ Введи номер клетки от 1 до 9.',
                                    reply_markup=game_action_reply('ttt'))
        result, board, notice = ttt_play(uid, pos)
        if result == 'invalid':
            return bot.send_message(chat_id, f'❌ {notice}', reply_markup=game_action_reply('ttt'))
        notice += '\n\n' + ttt_board_text(board)
        keyboard = game_action_reply('ttt') if result == 'playing' else game_mune_reply(uid)
        bot.send_message(chat_id, notice, parse_mode='HTML', reply_markup=keyboard)
        return

    # --- Duel ---
    if game_type == "duel" and text in {"⚔️ Атака","🛡️ Щит","🧪 Зелье"}:
        ghost_hp, player_hp = game[1], game[2]
        log = []
        if text == "⚔️ Атака":
            dmg = random.randint(20,35); ghost_hp -= dmg; log.append(f"⚔️ Урон снежному голему: <b>{dmg}</b>")
        elif text == "🛡️ Щит":
            log.append("🛡️ Ты встал в блок!")
        else:
            heal = random.randint(15,30); player_hp = min(100, player_hp+heal); log.append(f"🧪 Восстановлено <b>{heal} HP</b>!")
        if ghost_hp <= 0:
            add_coins(uid,75); add_xp(uid,50); try_drop_snowflake(uid); delete_active_game(uid)
            bot.send_message(chat_id, "🏆 <b>Победа!</b> Ты изгнал Голема!\n💰 +75 монет\n✨ +50 XP", parse_mode="HTML", reply_markup=game_mune_reply(uid))
            return
        dmg = random.randint(10,25) // (2 if text == "🛡️ Щит" else 1)
        player_hp -= dmg; log.append(f"⛄ Голем нанёс <b>{dmg}</b> урона!")
        if player_hp <= 0:
            delete_active_game(uid)
            bot.send_message(chat_id, "❄️ <b>Ты пал в бою...</b>", parse_mode="HTML", reply_markup=game_mune_reply(uid))
            return
        update_game_state(uid, secret_number=ghost_hp, attempts=player_hp)
        log_text = "\n".join(log)
        bot.send_message(chat_id, f"⚰️ <b>Битва со снежным големом</b>\n\n{log_text}\n\n❤️ HP: <b>{player_hp}</b> | ⛄ Голем: <b>{ghost_hp}</b>", parse_mode="HTML", reply_markup=game_action_reply("duel"))
        return

    # --- Trick or treat ---
    if game_type == "trick_or_treat" and text.startswith("🎁 Подарок"):
        outcomes = [("🎁 <b>Подарок!</b> Тебе повезло!",35),("⛄ <b>Пакость!</b> Потеряно монет!",-15),("🎆 <b>Джекпот!</b> Золотой подарок!",60)]
        result = random.choices(outcomes, weights=[60,30,10], k=1)[0]
        add_coins(uid,result[1])
        bot.send_message(chat_id, f"{result[0]}\n💰 Результат: <b>{result[1]:+} монет</b>", parse_mode="HTML", reply_markup=game_action_reply("trick"))
        return

    # --- Graveyard ---
    if game_type == "graveyard" and text == "🔎 Искать":
        loot = random.choices(["coins","item","nothing"],weights=[60,25,15])[0]
        if loot == "coins":
            found=random.randint(15,50); add_coins(uid,found); msg=f"💰 Нашёл <b>+{found} монет</b>!"
        elif loot == "item":
            item=random.choice(["❄️","🦴","🔮","📜"]); add_to_inventory(uid,item); msg=f"✨ Нашёл артефакт <b>{item}</b>!"
        else: msg="🧹 Ничего не нашёл..."
        try_drop_snowflake(uid)
        bot.send_message(chat_id, f"🔎 <b>Поиск сокровищ</b>\n\n{msg}", parse_mode="HTML", reply_markup=game_action_reply("graveyard"))
        return

@bot.message_handler(content_types=["text"], func=lambda message: bool(message.text) and not message.text.lstrip().startswith("/"))
def handle_messages(message):
    user_id = message.from_user.id

    # ===== РЫНОК ЭМОДЗИ =====
    if user_id in pending_market and not message.text.startswith('/'):
        emoji = pending_market.pop(user_id)

        try:
            price = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "❌ Цена должна быть целым числом!")
            return

        if price <= 0:
            bot.reply_to(message, "❌ Цена должна быть больше 0!")
            return

        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM inventory WHERE user_id=? AND item_type='emoji' AND item_value=? LIMIT 1",
            (user_id, emoji)
        )
        row = cursor.fetchone()

        if not row:
            bot.reply_to(message, "❌ Эмодзи больше нет в инвентаре!")
            return

        try:
            cursor.execute("DELETE FROM inventory WHERE id=?", (row[0],))
            cursor.execute(
                "INSERT INTO auction (seller_id, item_type, item_value, price) VALUES (?, 'emoji', ?, ?)",
                (user_id, emoji, price)
            )
            # Последняя копия ушла на рынок — автоматически снимаем эмодзи с профиля
            unequipped = unequip_if_no_copies(cursor, user_id, emoji)
            conn.commit()

            note = "\n👕 Эмодзи снят с профиля: у тебя не осталось копий." if unequipped else ""
            bot.reply_to(
                message,
                f"🏪 <b>{emoji} выставлен на рынок!</b>\n💰 Цена: <b>{price}</b> 🪙{note}",
                parse_mode="HTML"
            )
        except Exception as e:
            conn.rollback()
            print(f"[MARKET ERROR] {e}")
            bot.reply_to(message, "❌ Не удалось выставить эмодзи на рынок!")

        return

    # ===== ИГРЫ =====
    game_data = get_active_game(user_id)

    if not game_data or message.text.startswith('/'):
        return

    game_type = game_data[0]
    # Старые зимние механики заменены, незаконченные партии нельзя продолжать.
    if game_type in {'witch_guess','duel','trick_or_treat','graveyard'}:
        delete_active_game(user_id)
        return bot.send_message(message.chat.id,
            '❄️ Эти игры обновлены! Новые режимы появятся в зимнем меню.')

    # ===== УГАДАЙ ЧИСЛО =====
    if game_type == 'number':
        secret, attempts = game_data[1], game_data[2]

        try:
            guess = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "🔢 Пожалуйста, введи число!")
            return

        if not 1 <= guess <= 10:
            bot.reply_to(message, "🔢 Число должно быть от 1 до 10!")
            return

        if guess == secret:
            add_coins(user_id, 50)
            delete_active_game(user_id)
            bot.reply_to(message, f"🎉 <b>УРА!</b> Ты угадал число {secret}!\n💰 +50 монет", parse_mode="HTML")
        else:
            attempts -= 1
            if attempts <= 0:
                delete_active_game(user_id)
                bot.reply_to(message, f"❄️ <b>Попытки закончились!</b>\nЧисло было: {secret}", parse_mode="HTML")
            else:
                update_game_attempts(user_id, attempts)
                hint = "меньше" if guess > secret else "больше"
                bot.reply_to(message, f"🔢 Не угадал! Моё число {hint}.\n❤️ Осталось: {attempts}")

    # ===== КУБИК =====
    elif game_type == 'dice':
        secret = game_data[1]

        try:
            guess = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "🎲 Введи число от 1 до 6!")
            return

        if not 1 <= guess <= 6:
            bot.reply_to(message, "🎲 Число должно быть от 1 до 6!")
            return

        if guess == secret:
            add_coins(user_id, 100)
            add_xp(user_id, 25)
            bot.reply_to(message, f"🎲 <b>УГАДАЛ!</b>\n💰 +100 монет\n✨ +25 XP", parse_mode="HTML")
        else:
            bot.reply_to(message, f"❌ Неудача! Было число {secret}")

        delete_active_game(user_id)

    # ===== ВЗЛОМ КОДА =====
    elif game_type == 'code':
        secret_code, attempts = game_data[3], game_data[2]
        guess = message.text.strip()

        if len(guess) != 4 or not guess.isdigit():
            bot.reply_to(message, "🔐 Введи 4-значный код!")
            return

        if guess == secret_code:
            add_coins(user_id, 150)
            add_xp(user_id, 50)
            delete_active_game(user_id)
            bot.reply_to(message, "🎉 <b>КОД ВЗЛОМАН!</b>\n💰 +150 монет\n✨ +50 XP", parse_mode="HTML")
        else:
            attempts -= 1

            if attempts <= 0:
                delete_active_game(user_id)
                bot.reply_to(message, f"❄️ <b>КОД НЕ ВЗЛОМАН!</b>\nКод был: {secret_code}", parse_mode="HTML")
            else:
                update_game_attempts(user_id, attempts)
                matched = count_matched_digits(secret_code, guess)
                bot.reply_to(message, f"❌ Неправильно!\n💡 Совпадает цифр: {matched}\n❤️ Осталось: {attempts}")

    # ===== ЗИМНЕЕ ЧИСЛО =====
    elif game_type == 'witch_guess':
        secret, attempts = game_data[1], game_data[2]

        try:
            guess = int(message.text.strip())
        except ValueError:
            bot.reply_to(message, "🔮 Введи число!")
            return

        if guess == secret:
            add_coins(user_id, 50)
            add_xp(user_id, 30)
            delete_active_game(user_id)
            bot.reply_to(message, f"🎉 <b>Угадал число {secret}!</b>\n💰 +50 монет\n✨ +30 XP\n\n🎮 Выбери следующую игру:", parse_mode="HTML", reply_markup=_games_menu_markup(user_id))
        else:
            attempts -= 1

            if attempts <= 0:
                delete_active_game(user_id)
                bot.reply_to(message, f"❄️ <b>Попытки закончились!</b>\nЧисло было: <b>{secret}</b>\n\n🎮 Выбери следующую игру:", parse_mode="HTML", reply_markup=_games_menu_markup(user_id))
            else:
                update_game_state(user_id, secret_number=secret, attempts=attempts)
                hint = "БОЛЬШЕ ⬆️" if secret > guess else "МЕНЬШЕ ⬇️"
                bot.reply_to(message, f"🔮 Загаданное число: <b>{hint}</b>\n❤️ Попыток: <b>{attempts}</b>", parse_mode="HTML")
                
# ... весь остальной код ...

# ================= 🎄 MASTER BOT: ЗИМА 2026 =================
from datetime import timezone
from zoneinfo import ZoneInfo
from pathlib import Path as _WinterPath
import os as _winter_os

MASTER_MSK = ZoneInfo('Europe/Moscow')
MASTER_RELEASE = datetime(2026, 12, 1, 0, 0, tzinfo=MASTER_MSK)


def _master_setting(name, default='0'):
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        row = db.execute('SELECT value FROM system_settings WHERE key=?',(name,)).fetchone()
    return str(row[0]) if row else default


def _master_set_setting(name,value):
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('INSERT INTO system_settings(key,value) VALUES(?,?) '
                   'ON CONFLICT(key) DO UPDATE SET value=excluded.value',(name,str(value)))


def _master_unlock_current_games_once():
    """Включаем уже выпущенные игры после предыдущей версии с замочками.

    Делаем это однократно: последующие настройки /features сохранятся.
    """
    with sqlite3.connect(DB_NAME, timeout=15) as db:
        if db.execute("SELECT 1 FROM system_settings WHERE key='current_games_open_v3'").fetchone():
            return
        for game in ('ttt', 'maze', 'lights', 'penguin', 'cipher'):
            db.execute(
                'INSERT INTO system_settings(key, value) VALUES(?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('feature_' + game, '1'),
            )
        db.execute(
            "INSERT INTO system_settings(key,value) VALUES('current_games_open_v3','1')"
        )


_master_unlock_current_games_once()


def _master_staff(uid):
    try:
        user = openbot.get_user(uid)
        return isinstance(user,dict) and user.get('exists') and str(user.get('status','')).lower() in ('developer','owner','tester','admin')
    except Exception:
        return False


def _master_admin(uid):
    try:
        user = openbot.get_user(uid)
        return isinstance(user,dict) and user.get('exists') and str(user.get('status','')).lower() in ('developer','owner','admin')
    except Exception:
        return False


def _master_release_pending():
    return (_master_setting('winter_launch','1')=='1' and datetime.now(MASTER_MSK)<MASTER_RELEASE)


def _master_winter_init():
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS master_winter_launch_gifts ('
                   'user_id INTEGER PRIMARY KEY, claimed_at TEXT NOT NULL)')
    with sqlite3.connect(master_winter_db.get_db_path(),timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS december_claims ('
                   'user_id INTEGER NOT NULL, reward_day TEXT NOT NULL, claimed_at TEXT NOT NULL, '
                   'PRIMARY KEY(user_id,reward_day))')

_master_winter_init()


def _master_winter_bundle(uid,days=(),launch=False):
    """Atomic winter rewards across Master main DB + separate event DB."""
    event_path=master_winter_db.get_db_path()
    now=datetime.now(MASTER_MSK)
    awarded=0;coins=0;snow=0;cases=0;title=False
    with sqlite3.connect(DB_NAME,timeout=20) as db:
        db.execute('ATTACH DATABASE ? AS holiday',(event_path,))
        db.execute('BEGIN IMMEDIATE')
        exists=db.execute('SELECT 1 FROM users WHERE user_id=?',(uid,)).fetchone()
        if not exists:
            return (0,'❌ Создай профиль через /start.')
        if launch:
            stamp=now.isoformat()
            cur=db.execute('INSERT OR IGNORE INTO master_winter_launch_gifts(user_id,claimed_at) VALUES(?,?)',(uid,stamp))
            if cur.rowcount:
                awarded+=1;coins+=5000;snow+=25;cases+=1
                new=db.execute('INSERT OR IGNORE INTO holiday.titles(user_id,title_key,price,bought_at) VALUES(?,?,0,?)',
                           (uid,'winter_pioneer',stamp))
                title=bool(new.rowcount)
        for day in days:
            stamp=f'2026-12-{day:02d}'
            cur=db.execute('INSERT OR IGNORE INTO holiday.december_claims(user_id,reward_day,claimed_at) VALUES(?,?,?)',
                           (uid,stamp,now.isoformat()))
            if day==31:
                new=db.execute('INSERT OR IGNORE INTO holiday.titles(user_id,title_key,price,bought_at) VALUES(?,?,0,?)',
                           (uid,'winter_2027',now.isoformat()))
                title=title or bool(new.rowcount)
            if not cur.rowcount:
                continue
            awarded+=1
            if day in (10,20,25,31):cases+=1
            elif day%3==0:snow+=5+day//3
            else:coins+=200+60*day
        if coins:db.execute('UPDATE users SET coins=COALESCE(coins,0)+? WHERE user_id=?',(coins,uid))
        if snow:
            db.execute('INSERT INTO holiday.players(user_id,snowflakes,created_at) VALUES(?,?,?) '
                       'ON CONFLICT(user_id) DO UPDATE SET snowflakes=snowflakes+excluded.snowflakes',(uid,snow,now.isoformat()))
        if cases:
            db.execute("INSERT INTO holiday.case_inventory(user_id,case_key,count) VALUES(?,'gold',?) "
                       'ON CONFLICT(user_id,case_key) DO UPDATE SET count=count+excluded.count',(uid,cases))
    lines=[f'🎁 Получено подарков: {awarded}']
    if coins:lines.append(f'💰 +{coins} монет')
    if snow:lines.append(f'❄️ +{snow} снежинок')
    if cases:lines.append(f'🥇 +{cases} золотых кейсов')
    if title:lines.append('🏅 Новый титул в «Мои титулы»')
    if not awarded and not title: return 0,'✅ Всё уже получено.'
    return awarded+int(title and not awarded),'\n'.join(lines)


def _master_december_calendar(uid):
    now=datetime.now(MASTER_MSK)
    last=(now.day if now.year==2026 and now.month==12 else 31 if now.year>2026 else 0)
    with sqlite3.connect(master_winter_db.get_db_path(),timeout=15) as db:
        claimed={r[0] for r in db.execute('SELECT reward_day FROM december_claims WHERE user_id=?',(uid,))}
    kb=types.InlineKeyboardMarkup(row_width=5)
    available=0
    for first in range(1,32,5):
        buttons=[]
        for day in range(first,min(32,first+5)):
            key=f'2026-12-{day:02d}'
            icon='✅' if key in claimed else '🎁' if day<=last else '🔒'
            if icon=='🎁':available+=1
            buttons.append(types.InlineKeyboardButton(f'{icon} {day:02d}',callback_data=f'master_cal:day:{day}'))
        kb.row(*buttons)
    if available:kb.add(types.InlineKeyboardButton(f'🎁 Забрать все ({available})',callback_data='master_cal:all'))
    kb.row(types.InlineKeyboardButton('🔄 Обновить',callback_data='master_cal:refresh'),
           types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
    return ('🎄 <b>MASTER BOT · ДЕКАБРЬ 2026</b>\n\n'
            '📅 31 день — 31 подарок. Пропущенные подарки не сгорают.\n'
            f'🎁 Доступно: <b>{available}</b>\n\n'
            '🏅 31 декабря: золотой кейс и титул «Новый год 2027».\n'
            '⏰ Открытие каждого дня: 00:00 МСК.'),kb


@bot.message_handler(commands=['calendar','december'])
def master_calendar_command(message):
    text,kb=_master_december_calendar(message.from_user.id)
    bot.send_message(message.chat.id,text,reply_markup=kb,parse_mode='HTML')


@bot.callback_query_handler(func=lambda c:bool(c.data) and c.data.startswith('master_cal:'))
def master_calendar_cb(call):
    action=call.data.split(':')
    uid=call.from_user.id
    now=datetime.now(MASTER_MSK)
    last=(now.day if now.year==2026 and now.month==12 else 31 if now.year>2026 else 0)
    if action[1]=='day':
        try: day=int(action[2])
        except (ValueError,IndexError): day=0
        if day<1 or day>31 or day>last: return bot.answer_callback_query(call.id,'🔒 День ещё закрыт',show_alert=True)
        days=[day]
    elif action[1]=='all':days=list(range(1,last+1))
    elif action[1]=='refresh':days=[]
    else:return bot.answer_callback_query(call.id,'Неизвестное действие')
    if days:
        try:
            if openbot.get_global_ban(uid).get('banned') or check_ban(uid):
                return bot.answer_callback_query(call.id,'🚫 Награды недоступны при блокировке',show_alert=True)
        except Exception:
            return bot.answer_callback_query(call.id,'⚠️ Не удалось проверить ограничения',show_alert=True)
        try: count,notice=_master_winter_bundle(uid,days=days)
        except Exception:
            logging.getLogger('master_bot.winter').exception('calendar failure')
            return bot.answer_callback_query(call.id,'Ошибка выдачи наград',show_alert=True)
    else:count,notice=0,'🔄 Обновлено'
    bot.answer_callback_query(call.id,notice[:190],show_alert=bool(count))
    text,kb=_master_december_calendar(uid)
    try: bot.edit_message_text(text,chat_id=call.message.chat.id,message_id=call.message.message_id,
                               reply_markup=kb,parse_mode='HTML')
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():raise


def _master_release_view(*, staff_preview=False, force_waiting=False):
    """Winter screen. Staff can preview it without losing access to the bot."""
    now = datetime.now(MASTER_MSK)
    pending = now < MASTER_RELEASE and (
        _master_setting('winter_launch', '1') == '1' or force_waiting
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    if pending:
        diff = max(0, int((MASTER_RELEASE - now).total_seconds()))
        days, rest = divmod(diff, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, _ = divmod(rest, 60)
        text = (
            '🎄 <b>MASTER BOT · ЗИМНЕЕ ОБНОВЛЕНИЕ</b> ❄️\n'
            '━━━━━━━━━━━━━━━━━━\n\n'
            '⏳ <b>Ожидание открытия</b>\n'
            f'До релиза: <b>{days} д. {hours:02d} ч. {minutes:02d} мин.</b>\n'
            '📅 1 декабря 2026 года, 00:00 МСК\n\n'
            '🎁 Скоро откроются зимние развлечения и праздничные подарки!'
        )
        if force_waiting and _master_setting('winter_launch', '1') != '1':
            text += '\n\n👁 <i>Демонстрация: ожидание сейчас выключено.</i>'
        kb.add(types.InlineKeyboardButton('🔄 Обновить отсчёт', callback_data='master_launch:check'))
    else:
        text = (
            '🎉 <b>MASTER BOT · ЗИМНИЙ СЕЗОН</b>\n\n'
            '❄️ Ожидание завершено. Добро пожаловать!\n'
            '🎁 Получи подарок за открытие зимнего сезона.'
        )
        kb.add(types.InlineKeyboardButton('🎁 Войти и забрать подарок', callback_data='master_launch:play'))
    if staff_preview:
        text += '\n\n🧪 <i>Просмотр администратора. Для сотрудников бот остаётся доступен.</i>'
        kb.add(types.InlineKeyboardButton('🎮 Войти в бот (сотрудник)', callback_data='master_launch:staff_enter'))
    return text, kb


def _master_send_release(cid, mid=None, *, staff_preview=False, force_waiting=False):
    text, kb = _master_release_view(staff_preview=staff_preview, force_waiting=force_waiting)
    if mid is None:
        return bot.send_message(cid, text, reply_markup=kb, parse_mode='HTML')
    try:
        return bot.edit_message_text(text, chat_id=cid, message_id=mid,
                                     reply_markup=kb, parse_mode='HTML')
    except apihelper.ApiTelegramException as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


@bot.message_handler(commands=['launch'])
def master_launch_admin(message):
    if message.chat.type != 'private' or not _master_admin(message.from_user.id):
        return bot.reply_to(message, '⛔ Команда доступна администрации.')
    parts = (message.text or '').split()
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == 'status'):
        enabled = _master_setting('winter_launch', '1') == '1'
        status = '⏳ ВКЛЮЧЕНО' if enabled else '✅ ВЫКЛЮЧЕНО'
        return bot.reply_to(
            message,
            f'🎄 <b>ОЖИДАНИЕ ЗИМНЕГО ОБНОВЛЕНИЯ</b>\n\n'
            f'Состояние: <b>{status}</b>\n'
            '📅 Дата релиза: 01.12.2026, 00:00 МСК\n\n'
            '/launch on — показать ожидание игрокам\n'
            '/launch off — открыть бот\n'
            '/launch preview — посмотреть заставку',
            parse_mode='HTML',
        )
    if len(parts) != 2 or parts[1] not in ('on', 'off', 'preview'):
        return bot.reply_to(message, 'Формат: /launch on | off | preview | status')
    action = parts[1]
    if action == 'preview':
        return _master_send_release(message.chat.id, staff_preview=True, force_waiting=True)
    _master_set_setting('winter_launch', '1' if action == 'on' else '0')
    if action == 'on':
        if datetime.now(MASTER_MSK) >= MASTER_RELEASE:
            return bot.reply_to(message,
                '⚠️ Ожидание включено, но дата релиза уже прошла: 01.12.2026. '
                'Заставка с отсчётом после релиза не блокирует игроков.')
        # Важно: админ сразу ВИДИТ экран, хотя сам обходил ожидание.
        return _master_send_release(message.chat.id, staff_preview=True)
    return bot.send_message(
        message.chat.id,
        '✅ <b>ОЖИДАНИЕ ОТКЛЮЧЕНО</b>\n\nБот снова доступен обычным игрокам.',
        reply_markup=main_mune(), parse_mode='HTML',
    )


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith('master_launch:'))
def master_launch_cb(call):
    uid = call.from_user.id
    action = call.data
    staff = _master_staff(uid)
    if action == 'master_launch:staff_enter':
        if not staff:
            return bot.answer_callback_query(call.id, '⛔ Только для сотрудников', show_alert=True)
        bot.answer_callback_query(call.id)
        return bot.edit_message_text(
            '🏠 <b>MASTER BOT · ГЛАВНОЕ МЕНЮ</b>\n\n🧪 Режим сотрудника — доступ открыт.',
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=main_mune(), parse_mode='HTML',
        )
    if action == 'master_launch:check':
        bot.answer_callback_query(call.id, '🔄 Отсчёт обновлён')
        return _master_send_release(call.message.chat.id, call.message.message_id,
                                    staff_preview=staff)
    if action != 'master_launch:play':
        return bot.answer_callback_query(call.id, 'Неизвестная кнопка', show_alert=True)
    if _master_release_pending():
        bot.answer_callback_query(call.id, '⏳ Релиз ещё не наступил')
        return _master_send_release(call.message.chat.id, call.message.message_id,
                                    staff_preview=staff)
    try:
        try:
            if openbot.get_global_ban(uid).get('banned') or _punishment_get(uid):
                return bot.answer_callback_query(call.id, '🚫 Подарки недоступны при блокировке', show_alert=True)
        except Exception:
            return bot.answer_callback_query(call.id, '⚠️ Openbot ID недоступен', show_alert=True)
        create_user(uid, call.from_user.username, call.from_user.first_name or 'Игрок')
        count, notice = _master_winter_bundle(uid, launch=True)
    except Exception:
        logging.getLogger('master_bot.winter').exception('launch gift failure')
        return bot.answer_callback_query(call.id, 'Не удалось выдать подарок', show_alert=True)
    bot.answer_callback_query(call.id, notice[:190], show_alert=bool(count))
    bot.edit_message_text('🎄 <b>Добро пожаловать в зимний Master Bot!</b>\n\n' + notice,
                          chat_id=call.message.chat.id, message_id=call.message.message_id,
                          reply_markup=main_mune(), parse_mode='HTML')


@bot.message_handler(commands=['menu'])
def master_menu_command(message):
    if get_user_settings(message.from_user.id)[0]=='reply':
        send_main_reply_menu(message.chat.id,message.from_user.first_name)
    else:
        bot.send_message(message.chat.id,'🎄 <b>MASTER BOT · ГЛАВНОЕ МЕНЮ</b>\n\n❄️ Выбирай раздел:',reply_markup=main_mune(),parse_mode='HTML')


def _master_owned_titles(uid):
    names={'none':'Без титула'}
    try:
        for key in master_winter_db.get_titles(uid):
            if key in PROFILE_TITLES:names[key]=PROFILE_TITLES[key]
    except Exception:pass
    row=conn.execute('SELECT profile_title FROM users WHERE user_id=?',(uid,)).fetchone()
    if row and row[0] in PROFILE_TITLES:names[row[0]]=PROFILE_TITLES[row[0]]
    return names


@bot.message_handler(commands=['my_titles'])
def master_my_titles_command(message):
    names=_master_owned_titles(message.from_user.id)
    kb=types.InlineKeyboardMarkup(row_width=1)
    for key,name in names.items():kb.add(types.InlineKeyboardButton(name,callback_data=f'master_title:{key}'))
    kb.add(types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
    bot.send_message(message.chat.id,'🏆 <b>МОИ ТИТУЛЫ</b>\nВыбери титул:',reply_markup=kb,parse_mode='HTML')


@bot.callback_query_handler(func=lambda c:bool(c.data) and c.data.startswith('master_title:'))
def master_title_cb(call):
    key=call.data.split(':',1)[1]
    if key not in _master_owned_titles(call.from_user.id):
        return bot.answer_callback_query(call.id,'Титул не получен',show_alert=True)
    conn.execute('UPDATE users SET profile_title=? WHERE user_id=?',(None if key=='none' else key,call.from_user.id));conn.commit()
    bot.answer_callback_query(call.id,'✅ Титул установлен')
    bot.edit_message_text('🏅 <b>Титул установлен!</b>',chat_id=call.message.chat.id,
                          message_id=call.message.message_id,reply_markup=main_mune(),parse_mode='HTML')


@bot.message_handler(commands=['leaders'])
def master_leaders_command(message):
    users=conn.execute("SELECT COALESCE(first_name,username,'Игрок'),coins FROM users WHERE status!='banned' ORDER BY coins DESC LIMIT 10").fetchall()
    text='🏆 <b>MASTER BOT · ЗИМНИЕ ЛИДЕРЫ</b>\n\n'+('\n'.join(
        f'{i}. {html.escape(str(name))} — <b>{int(coins or 0):,}</b> 🪙' for i,(name,coins) in enumerate(users,1)) if users else 'Пока пусто.')
    kb=types.InlineKeyboardMarkup();kb.add(types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
    bot.send_message(message.chat.id,text,reply_markup=kb,parse_mode='HTML')



# Replaced old handler master_tech_admin with Winter command parity implementation.


class MasterWinterLaunchMiddleware(BaseMiddleware):
    """Before release: normal players see the waiting screen; staff can preview and enter."""

    def __init__(self):
        super().__init__()
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update, data):
        user = getattr(update, 'from_user', None)
        if not user or getattr(update, 'successful_payment', None) is not None:
            return None
        is_callback = hasattr(update, 'data')
        value = (update.data or '') if is_callback else (getattr(update, 'text', '') or '').strip()
        cmd = value.split(maxsplit=1)[0].split('@')[0].lower() if value.startswith('/') else ''

        # Сотрудники могут тестировать все разделы, даже когда ожидание включено.
        # /start при этом показывает им заставку с кнопкой входа для персонала.
        if _master_staff(user.id):
            if not is_callback and cmd == '/start' and _master_release_pending():
                _master_send_release(update.chat.id, staff_preview=True)
                return CancelUpdate()
            return None

        # Кнопки самого экрана ожидания должны работать.
        if is_callback and value.startswith('master_launch:'):
            return None

        # Информация о блокировках и поддержка остаются доступны.
        if cmd in {'/ban_info', '/appeal', '/support', '/help'}:
            return None

        # Пока ожидание включено, ни старые кнопки, ни Reply, ни /start
        # не должны открывать основной интерфейс обычному игроку.
        if _master_release_pending():
            if is_callback:
                bot.answer_callback_query(update.id, '⏳ Ожидаем зимнее обновление')
                _master_send_release(update.message.chat.id, update.message.message_id)
            else:
                _master_send_release(update.chat.id)
            return CancelUpdate()

        # После релиза показываем подарок тем, кто ещё не получил его.
        if cmd == '/start' and _master_setting('winter_launch', '1') == '1':
            row = conn.execute(
                'SELECT 1 FROM master_winter_launch_gifts WHERE user_id=?',
                (user.id,),
            ).fetchone()
            if not row:
                _master_send_release(update.chat.id)
                return CancelUpdate()
        return None

    def post_process(self, update, data, exception):
        pass


bot.setup_middleware(MasterWinterLaunchMiddleware())


def master_leaders_callback(call):
    users=conn.execute("SELECT COALESCE(first_name,username,'Игрок'),coins FROM users WHERE status!='banned' ORDER BY coins DESC LIMIT 10").fetchall()
    text='🏆 <b>MASTER BOT · ЗИМНИЕ ЛИДЕРЫ</b>\n\n'+('\n'.join(f'{i}. {html.escape(str(name))} — <b>{int(coins or 0):,}</b> 🪙' for i,(name,coins) in enumerate(users,1)) if users else 'Пока пусто.')
    kb=types.InlineKeyboardMarkup();kb.add(types.InlineKeyboardButton('🏠 Главное меню',callback_data='back'))
    bot.edit_message_text(text,chat_id=call.message.chat.id,message_id=call.message.message_id,reply_markup=kb,parse_mode='HTML')
    bot.answer_callback_query(call.id)


MASTER_SNOW_TITLES = {
    'winter_2027': ('🎆 Новый год 2027',90),
    'snow_collector': ('❄️ Снежный коллекционер',150),
    'newyear_elf': ('🧝 Новогодний эльф',12),
    'christmas_bell': ('🔔 Звонарь Рождества',20),
    'gingerbread': ('🍪 Имбирный пряник',25),
    'tinsel_master': ('🎀 Мастер мишуры',35),
    'santa_assistant': ('🎅 Помощник Деда Мороза',45),
    'fir_guardian': ('🎄 Страж новогодней ёлки',55),
    'gift_legend': ('🎁 Легенда подарков',70),
    'ice_star': ('🌠 Ледяная звезда',85),
    'midnight_chimes': ('🕛 Хранитель курантов',100),
    'frost_emperor': ('👑 Император Мороза',130),
    'snowstorm_king': ('🌨 Король метели',175),
    'polar_legend': ('🌌 Легенда полярной зимы',220),
}
for _snow_key, (_snow_title, _snow_price) in MASTER_SNOW_TITLES.items():
    PROFILE_TITLES[_snow_key] = _snow_title


def _master_snow_menu(uid, page=0):
    items, page, total = _titles_page(list(MASTER_SNOW_TITLES.items()), page)
    owned = _master_owned_titles(uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, (name, price) in items:
        label = f'✅ {name} · получен' if key in owned else f'{name} — {price} ❄️'
        kb.add(types.InlineKeyboardButton(label, callback_data=f'master_snow_buy:{key}'))
    navigation = []
    if page > 0:
        navigation.append(types.InlineKeyboardButton('⬅️ Назад', callback_data=f'master_snow_page:{page-1}'))
    navigation.append(types.InlineKeyboardButton(f'📄 {page+1}/{total}', callback_data=f'master_snow_page:{page}'))
    if page < total-1:
        navigation.append(types.InlineKeyboardButton('Вперёд ➡️', callback_data=f'master_snow_page:{page+1}'))
    kb.row(*navigation)
    kb.add(types.InlineKeyboardButton('🛍 В зимнюю лавку', callback_data='shop'))
    text = (
        '❄️ <b>НОВОГОДНИЕ ТИТУЛЫ ЗА СНЕЖИНКИ</b>\n'
        '━━━━━━━━━━━━━━━━━━\n'
        f'❄️ Баланс: <b>{master_winter_db.get_keys(uid)}</b> снежинок\n'
        f'📖 Страница <b>{page+1}/{total}</b>\n\n'
        'Выбери новогодний титул. Полученные титулы находятся в «Мои титулы».\n'
        '🎆 Титул «Новый год 2027» можно получить бесплатно 31 декабря.'
    )
    return text, kb


def master_snow_shop_callback(call):
    page = 0
    if (call.data or '').startswith('master_snow_page:'):
        try:
            page = int(call.data.partition(':')[2])
        except (ValueError, TypeError):
            return bot.answer_callback_query(call.id, 'Неверная страница', show_alert=True)
    text, kb = _master_snow_menu(call.from_user.id, page)
    bot.answer_callback_query(call.id)
    _titles_edit_message(call, text, kb)


def master_snow_buy_callback(call):
    key = call.data.partition(':')[2]
    uid = call.from_user.id
    item = MASTER_SNOW_TITLES.get(key)
    if item is None:
        return bot.answer_callback_query(call.id, 'Титул не найден', show_alert=True)
    if key in _master_owned_titles(uid):
        return bot.answer_callback_query(call.id, '✅ Уже получен', show_alert=True)
    name, price = item
    if not master_winter_db.spend_keys(uid, price, 'title:' + key):
        return bot.answer_callback_query(call.id, '❄️ Недостаточно снежинок', show_alert=True)
    try:
        added = master_winter_db.record_title(uid, key, price)
    except Exception:
        master_winter_db.add_keys(uid, price, 'refund_title:' + key)
        raise
    if not added:
        master_winter_db.add_keys(uid, price, 'refund_duplicate:' + key)
        return bot.answer_callback_query(call.id, '✅ Уже получен', show_alert=True)
    bot.answer_callback_query(call.id, f'🎉 Титул «{name}» получен!', show_alert=True)
    page = list(MASTER_SNOW_TITLES).index(key) // TITLE_CATALOG_PAGE_SIZE
    text, kb = _master_snow_menu(uid, page)
    _titles_edit_message(call, text, kb)

@bot.pre_checkout_query_handler(func=lambda q:True)
def master_pre_checkout(query):
    parts=(query.invoice_payload or '').split(':')
    ok=(len(parts)==3 and parts[0]=='stars' and parts[1] in STARS_SHOP
        and parts[2]==str(query.from_user.id))
    if ok:
        try:ok=not openbot.get_global_ban(query.from_user.id).get('banned')
        except Exception:ok=False
    bot.answer_pre_checkout_query(query.id,ok=bool(ok),error_message=None if ok else 'Покупка недоступна')


@bot.message_handler(content_types=['successful_payment'])
def master_stars_paid(message):
    payment=message.successful_payment
    parts=(payment.invoice_payload or '').split(':')
    if len(parts)!=3 or parts[0]!='stars' or parts[2]!=str(message.from_user.id):return
    key=parts[1];uid=message.from_user.id
    if key not in STARS_SHOP:return
    # И запись Stars, и выдача снежинок — одна транзакция, двойная выдача исключена.
    with sqlite3.connect(DB_NAME,timeout=15) as db:
        db.execute('ATTACH DATABASE ? AS holiday',(master_winter_db.get_db_path(),))
        db.execute('CREATE TABLE IF NOT EXISTS master_stars_payments ('
                   'charge_id TEXT PRIMARY KEY,user_id INTEGER,product TEXT)')
        db.execute('BEGIN IMMEDIATE')
        cur=db.execute('INSERT OR IGNORE INTO master_stars_payments(charge_id,user_id,product) VALUES(?,?,?)',
                       (payment.telegram_payment_charge_id,uid,key))
        if cur.rowcount!=1:return
        if key=='coins_10m':db.execute('UPDATE users SET coins=COALESCE(coins,0)+10000000 WHERE user_id=?',(uid,))
        elif key=='level_20':db.execute('UPDATE users SET level=MAX(COALESCE(level,1),20) WHERE user_id=?',(uid,))
        elif key=='snowflakes_25':
            db.execute('INSERT INTO holiday.players(user_id,snowflakes,created_at) VALUES(?,25,?) '
                       'ON CONFLICT(user_id) DO UPDATE SET snowflakes=snowflakes+25',
                       (uid,datetime.now(MASTER_MSK).isoformat()))
    bot.send_message(message.chat.id,'✅ Оплата получена! Награда начислена.')

# ==============================
# Глобальный обработчик ошибок
# ==============================

# Общая папка logs находится над папками Random_bot и Master_bot.
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "master_bot.log"
error_logger = logging.getLogger("master_bot.errors")
error_logger.setLevel(logging.ERROR)
error_logger.propagate = False
if not error_logger.handlers:
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024,
        backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    error_logger.addHandler(file_handler)
    # Сетевые ошибки polling могут логироваться самой библиотекой telebot.
    for library_logger_name in ("TeleBot", "telebot"):
        library_logger = logging.getLogger(library_logger_name)
        library_logger.addHandler(file_handler)
        library_logger.setLevel(logging.WARNING)

class GlobalExceptionHandler(telebot.ExceptionHandler):

    def handle(self, exception):

        print("\n" + "=" * 60)
        print("🚨 ОШИБКА В БОТЕ")
        print("=" * 60)

        # Получаем полный traceback
        error_log = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))

        # Записываем любой traceback, независимо от настроек Telegram.
        error_logger.error("\n%s", error_log.rstrip())

        print(error_log)

        print("=" * 60)

        # Логи ошибок идут в чат разработчика (DEVELOPER_CHAT_ID)
        developers = [DEVELOPER_CHAT_ID]

        # ==============================
        # Отправка ошибки разработчикам
        # ==============================

        for developer_id in developers:

            # Настройка уведомлений берётся из основной базы пользователей.
            # Если прочитать её не удалось, не отправляем сообщение,
            # а оставляем информацию в локальном логе.
            try:
                if get_user_settings(developer_id)[1] != 1:
                    continue
            except Exception:
                error_logger.exception("Не удалось проверить уведомления для %s", developer_id)
                continue

            try:

                # Первое сообщение
                bot.send_message(
                    developer_id,
                    "🚨 <b>ОШИБКА В БОТЕ</b>\n\n"
                    "Произошла непредвиденная ошибка.\n"
                    "📋 Полный лог ошибки:",
                    parse_mode="HTML"
                )

                # Telegram ограничивает сообщения примерно 4096 символами.
                # Оставляем запас.
                max_length = 3900

                # Разбиваем длинный traceback
                parts = [
                    error_log[i:i + max_length]
                    for i in range(0, len(error_log), max_length)
                ]

                for number, part in enumerate(parts, start=1):

                    # Экранируем HTML
                    safe_part = html.escape(part)

                    # Добавляем номер части только если их несколько
                    if len(parts) > 1:
                        header = f"📄 <b>Часть {number}/{len(parts)}</b>\n\n"
                    else:
                        header = ""

                    bot.send_message(
                        developer_id,
                        f"{header}<pre>{safe_part}</pre>",
                        parse_mode="HTML"
                    )

                print(
                    f"[ERROR] Лог отправлен разработчику "
                    f"{developer_id}"
                )

            except Exception as send_error:

                error_logger.exception("Не удалось отправить ошибку разработчику %s", developer_id)
                print(
                    f"[ERROR] Не удалось отправить ошибку "
                    f"разработчику {developer_id}: {send_error}"
                )

        # True означает, что исключение обработано
        return True


# ==============================
# Подключаем обработчик
# ==============================

bot.exception_handler = GlobalExceptionHandler()


print("[ Успешно ] Бот запущен и готов к работе!")

bot.polling(non_stop=True, timeout=70)