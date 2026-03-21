import telebot
import sqlite3
import random
import signal
from datetime import datetime, timedelta
from collections import defaultdict
from telebot import types

bot = telebot.TeleBot('8493714047:AAESLbWrHNcB7xCeH3RbybiqSUdp0zJ6i90')

# ========== НАСТРОЙКИ ==========
DEFAULT_DIFFICULTY = "normal"

# ========== ДОСТИЖЕНИЯ ЗА УРОВНИ ==========
# reward_type: 'coins', 'title', 'emoji'
LEVEL_ACHIEVEMENTS = {
    "lvl_5": {
        "name": "⚡ Опытный",
        "desc": "Достигни 5 уровня",
        "target": 5,
        "reward_type": "coins",
        "reward_val": 100
    },
    "lvl_10": {
        "name": "🎖 Ветеран",
        "desc": "Достигни 10 уровня",
        "target": 10,
        "reward_type": "title",
        "reward_val": "veteran"
    },
    "lvl_15": {
        "name": "🦁 Хищник",
        "desc": "Достигни 15 уровня",
        "target": 15,
        "reward_type": "emoji",
        "reward_val": "🦁"
    },
    "lvl_25": {
        "name": "👑 Король",
        "desc": "Достигни 25 уровня",
        "target": 25,
        "reward_type": "title",
        "reward_val": "king"
    },
    "lvl_50": {
        "name": "🌟 Легенда",
        "desc": "Достигни 50 уровня",
        "target": 50,
        "reward_type": "title",
        "reward_val": "legend_50"
    }
}

PROFILE_TITLES = {
    "none": "Без титула",
    "veteran": "🎖 Ветеран",
    "king": "👑 Король",
    "legend_50": "🌟 Легенда",
    "pro": "🔥 Профи",
    "legend": "👑 Легенда",
}

# ========== ПУЛ ЕЖЕДНЕВНЫХ ЗАДАНИЙ ==========
DAILY_POOL = [
    {"id": "d_win_3", "name": "🥇 Легкий старт", "desc": "Победи 3 раза в любой игре", "type": "wins", "target": 3, "reward": 50},
    {"id": "d_win_5", "name": "🔥 Опытный боец", "desc": "Победи 5 раз в любой игре", "type": "wins", "target": 5, "reward": 100},
    {"id": "d_tic_2", "name": "❌ Крестик-нос", "desc": "Победи 2 раза в Крестики-Нолики", "type": "tic_wins", "target": 2, "reward": 60},
    {"id": "d_coins_150", "name": "💰 Копилка", "desc": "Заработай 150 монет в играх", "type": "coins", "target": 150, "reward": 40},
    {"id": "d_games_10", "name": "🎮 Игроман", "desc": "Сыграй 10 партий в любой игре", "type": "games", "target": 10, "reward": 30},
]

# ========== СТАТУСЫ ==========
STATUS = {
    "developer": "🌐💠 Openbot.Ai",
    "coder": "🌐 Кодер",
    "admin": "⭐ Администратор",
    "user": "👤 Игрок",
    "banned": "🚫 Забаненный",
}

# ========== МАГАЗИН ТИТУЛОВ ==========
SHOP_ITEMS = {
    "pro": {
        "name": PROFILE_TITLES["pro"],
        "price": 500,
        "desc": "Крутой статус для профи"
    },
    "legend": {
        "name": PROFILE_TITLES["legend"],
        "price": 2000,
        "desc": "Легендарный статус"
    }
}

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect("Master_bot.db", check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            first_name TEXT,
            statys TEXT DEFAULT 'user',
            coins INTEGER DEFAULT 0,
            total_games INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            difficulty TEXT DEFAULT '{DEFAULT_DIFFICULTY}',
            profile_title TEXT DEFAULT NULL,
            profile_emoji TEXT DEFAULT NULL,
            level INTEGER DEFAULT 1,
            XP INTEGER DEFAULT 100,
            tic_wins INTEGER DEFAULT 0,
            rps_wins INTEGER DEFAULT 0
        )
    """)

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN tic_wins INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN rps_wins INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_games (
            user_id INTEGER PRIMARY KEY,
            game_name TEXT,
            game_key TEXT,
            secret_number INTEGER,
            attempts INTEGER,
            started_at TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_config (
            date TEXT PRIMARY KEY,
            quest_ids TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_daily_progress (
            user_id INTEGER,
            quest_id TEXT,
            progress INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, quest_id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_level_achievements (
            user_id INTEGER,
            achv_id TEXT,
            claimed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, achv_id)
        )
    """)
    conn.commit()

init_db()

# ========== DATABASE ==========
def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()

def create_user(user_id, username, first_name):
    cursor.execute(
        "INSERT OR IGNORE INTO users (id, username, first_name) VALUES (?, ?, ?)",
        (user_id, username.lower() if username else None, first_name)
    )
    conn.commit()

def add_coins(user_id, amount):
    cursor.execute("UPDATE users SET coins = coins + ? WHERE id = ?", (amount, user_id))
    conn.commit()
    update_daily_progress(user_id, "coins", amount)

def update_stats(user_id, won):
    cursor.execute("UPDATE users SET total_games = total_games + 1 WHERE id = ?", (user_id,))
    
    # Добавляем прогресс для задания "Сыграть N партий" (любые игры)
    update_daily_progress(user_id, "games", 1)
    
    if won:
        cursor.execute("UPDATE users SET wins = wins + 1 WHERE id = ?", (user_id,))
        update_daily_progress(user_id, "wins", 1)
    conn.commit()

def add_tic_win(user_id):
    cursor.execute("UPDATE users SET tic_wins = tic_wins + 1 WHERE id = ?", (user_id,))
    conn.commit()
    update_daily_progress(user_id, "tic_wins", 1)

def add_rps_win(user_id):
    cursor.execute("UPDATE users SET rps_wins = rps_wins + 1 WHERE id = ?", (user_id,))
    conn.commit()

def get_difficulty(user_id):
    cursor.execute("SELECT difficulty FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row else DEFAULT_DIFFICULTY

def set_difficulty(user_id, diff):
    cursor.execute("UPDATE users SET difficulty = ? WHERE id = ?", (diff, user_id))
    conn.commit()

def get_title(user_id):
    cursor.execute("SELECT profile_title FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else "none"

def set_title(user_id, title_key):
    cursor.execute("UPDATE users SET profile_title = ? WHERE id = ?", (title_key, user_id))
    conn.commit()

def set_emoji(user_id, emoji):
    cursor.execute("UPDATE users SET profile_emoji = ? WHERE id = ?", (emoji, user_id))
    conn.commit()

# ========== СИСТЕМА ЕЖЕДНЕВНЫХ ЗАДАНИЙ ==========
def check_and_refresh_daily_quests():
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT quest_ids FROM daily_config WHERE date = ?", (today,))
    row = cursor.fetchone()
    if row: return 
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute("SELECT quest_ids FROM daily_config WHERE date = ?", (yesterday,))
    prev_row = cursor.fetchone()
    prev_ids = set(prev_row[0].split(',')) if prev_row else set()
    available = [q for q in DAILY_POOL if q['id'] not in prev_ids]
    if len(available) < 2:
        available = DAILY_POOL
    selected = random.sample(available, min(2, len(available)))
    new_ids = ",".join([q['id'] for q in selected])
    cursor.execute("INSERT INTO daily_config (date, quest_ids) VALUES (?, ?)", (today, new_ids))
    conn.commit()
    cursor.execute("DELETE FROM user_daily_progress")
    conn.commit()

def update_daily_progress(user_id, progress_type, amount=1):
    check_and_refresh_daily_quests()
    cursor.execute("SELECT quest_ids FROM daily_config ORDER BY date DESC LIMIT 1")
    config = cursor.fetchone()
    if not config: return
    active_ids = config[0].split(',')
    for q_id in active_ids:
        quest = next((q for q in DAILY_POOL if q['id'] == q_id), None)
        if quest and quest['type'] == progress_type:
            cursor.execute("""
                INSERT INTO user_daily_progress (user_id, quest_id, progress)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, quest_id) DO UPDATE SET progress = progress + ?
            """, (user_id, q_id, amount, amount))
            conn.commit()

# ========== СИСТЕМА УРОВНЕЙ ==========
def check_level_up(user_id):
    cursor.execute("SELECT xp, level FROM users WHERE id = ?", (user_id,))
    xp, level = cursor.fetchone()
    new_level = xp // 200 + 1
    if new_level > level:
        cursor.execute("UPDATE users SET level = ? WHERE id = ?", (new_level, user_id))
        conn.commit()
        bot.send_message(user_id, f"🎉 Новый уровень: {new_level}!\nПроверь достижения! 🏆")

def add_xp(user_id, amount):
    cursor.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (amount, user_id))
    conn.commit()
    check_level_up(user_id)

# ================== КРЕСТИКИ-НОЛИКИ ==================

tic_games = {}

def t_check_winner(b):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for w in wins:
        if b[w[0]] == b[w[1]] == b[w[2]] != ' ':
            return b[w[0]]
    return 'Draw' if ' ' not in b else None


def t_minimax(board, depth, is_max):
    res = t_check_winner(board)
    if res == 'O': return 10 - depth
    if res == 'X': return depth - 10
    if res == 'Draw': return 0

    if is_max:
        best = -1000
        for i in range(9):
            if board[i] == ' ':
                board[i] = 'O'
                best = max(best, t_minimax(board, depth+1, False))
                board[i] = ' '
        return best
    else:
        best = 1000
        for i in range(9):
            if board[i] == ' ':
                board[i] = 'X'
                best = min(best, t_minimax(board, depth+1, True))
                board[i] = ' '
        return best


def t_best_move(board, difficulty):
    import random

    if difficulty == "easy":
        empty = [i for i in range(9) if board[i] == ' ']
        return random.choice(empty)

    # ИСПРАВЛЕНО: Правильная проверка на пустую клетку для случайного хода
    if difficulty == "normal" and random.random() < 0.3:
        empty = [i for i in range(9) if board[i] == ' ']
        return random.choice(empty)

    # Логика Minimax
    best_val = -1000
    move = -1
    for i in range(9):
        if board[i] == ' ':
            board[i] = 'O'
            val = t_minimax(board, 0, False)
            board[i] = ' '
            if val > best_val:
                best_val = val
                move = i
    return move


def t_keyboard(board):
    kb = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for i in range(9):
        text = board[i] if board[i] != ' ' else ' '
        buttons.append(types.InlineKeyboardButton(text, callback_data=f"t_{i}"))
    kb.add(*buttons)
    kb.add(types.InlineKeyboardButton("🚪 Выйти", callback_data="t_exit"))
    return kb

# ================== RPS ==================
RPS_MOVES = {0: "🪨 Камень", 1: "📄 Бумага", 2: "✂️ Ножницы"}
RPS_HISTORY = defaultdict(list)

def rps_predict_move(user_id):
    history = RPS_HISTORY.get(user_id, [])
    if len(history) < 2:
        return random.randint(0, 2)
    last_move = history[-1]
    potential_next_moves = []
    for i in range(len(history) - 1):
        if history[i] == last_move:
            potential_next_moves.append(history[i+1])
    if potential_next_moves:
        predicted = max(set(potential_next_moves), key=potential_next_moves.count)
        return (predicted + 1) % 3
    return random.randint(0, 2)

def rps_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("🪨", callback_data="rps_0"),
        types.InlineKeyboardButton("📄", callback_data="rps_1"),
        types.InlineKeyboardButton("✂️", callback_data="rps_2")
    )
    kb.add(types.InlineKeyboardButton("🚪 Выход", callback_data="rps_exit"))
    return kb

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def difficulty_menu(user_id):
    current = get_difficulty(user_id)
    icons = {"easy": "🟢", "normal": "🟡", "hard": "🔴"}
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(f"{icons['easy']} Лёгко {'✅' if current=='easy' else ''}", callback_data="diff_easy"),
        types.InlineKeyboardButton(f"{icons['normal']} Нормально {'✅' if current=='normal' else ''}", callback_data="diff_normal"),
        types.InlineKeyboardButton(f"{icons['hard']} Хард {'✅' if current=='hard' else ''}", callback_data="diff_hard"),
    )
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="back_main"))
    return kb

def save_game(user_id, name, key, secret, attempts):
    cursor.execute("""
        INSERT OR REPLACE INTO active_games
        (user_id, game_name, game_key, secret_number, attempts, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, name, key, secret, attempts, datetime.now().isoformat()))
    conn.commit()

def get_active_game(user_id):
    cursor.execute("SELECT * FROM active_games WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def delete_active_game(user_id):
    cursor.execute("DELETE FROM active_games WHERE user_id = ?", (user_id,))
    conn.commit()

def is_playing(user_id):
    return get_active_game(user_id) is not None

def get_progress_bar(current, target):
    percent = min(100, int((current / target) * 100))
    filled = percent // 10
    bar = "█" * filled + "░" * (10 - filled)
    return f"`{bar}` {current}/{target} ({percent}%)"

# ========== МЕНЮ ==========
def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎮 Игры", callback_data="game"),
        types.InlineKeyboardButton("🙎‍♂️ Профиль", callback_data="prof"),
        types.InlineKeyboardButton("📋 Ежедневные", callback_data="dailies"),
        types.InlineKeyboardButton("🏆 Достижения", callback_data="achievements"),
        types.InlineKeyboardButton("🛒 Магазин", callback_data="shop"),
        types.InlineKeyboardButton("⚙ Настройки", callback_data='set'),
    )
    return kb

def games_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🍉 Арбуз", callback_data="game_watermelon"),
        types.InlineKeyboardButton("🌞 Солнце", callback_data="game_sun"),
        types.InlineKeyboardButton("🍹 Лимонад", callback_data="game_lemonade"),
        types.InlineKeyboardButton("🏝️ Клад", callback_data="game_beach"),
        types.InlineKeyboardButton("❌Крестики нолики⭕", callback_data='crest'),
        types.InlineKeyboardButton("🪨 Камень Ножницы Бумага", callback_data='rps')
    )
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="back_main "))
    return kb

def setting():
    mar = types.InlineKeyboardMarkup(row_width=1)
    mar.add(
        types.InlineKeyboardButton('🎯Сложность', callback_data='level_hard'),
        types.InlineKeyboardButton('👤 Профиль', callback_data='set_prof'),
        types.InlineKeyboardButton('⬅  Назад', callback_data='back_main'),
    )
    return mar

def emoji_menu(user_id):
    cursor.execute("SELECT profile_emoji FROM users WHERE id = ?", (user_id,))
    current = cursor.fetchone()[0]
    emojis = ["😎", "🔥", "🎯", "👑", "🍉", "💎"]
    kb = types.InlineKeyboardMarkup(row_width=3)
    for e in emojis:
        text = e + (" ✅" if current == e else "")
        kb.add(types.InlineKeyboardButton(text, callback_data=f"emoji_{e}"))
    kb.add(types.InlineKeyboardButton("❌ Убрать эмоджи", callback_data="clear"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="set_prof"))
    return kb

def shop_menu(user_id):
    user = get_user(user_id)
    coins = user[4]
    current_title = user[8]
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = f"🛒 *МАГАЗИН ТИТУЛОВ*\n\n💰 Твои монеты: {coins}\n\n"
    for key, item in SHOP_ITEMS.items():
        is_owned = (current_title == key)
        status = "✅ Куплено" if is_owned else f"💰 {item['price']}"
        text += f"{item['name']} — {status}\n"
        kb.add(types.InlineKeyboardButton(
            f"Купить: {item['name']}" if not is_owned else "Уже есть",
            callback_data=f"buy_{key}"
        ))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="back_main"))
    return kb, text

# --- МЕНЮ ЕЖЕДНЕВНЫХ ЗАДАНИЙ ---
def dailies_menu(user_id):
    check_and_refresh_daily_quests()
    user = get_user(user_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = "📅 *ЕЖЕДНЕВНЫЕ ЗАДАНИЯ*\n\nВыполняй задания, чтобы получить монеты!\n\n"
    cursor.execute("SELECT quest_ids FROM daily_config ORDER BY date DESC LIMIT 1")
    config = cursor.fetchone()
    has_daily = False
    if config:
        active_ids = config[0].split(',')
        for q_id in active_ids:
            quest = next((q for q in DAILY_POOL if q['id'] == q_id), None)
            if quest:
                has_daily = True
                cursor.execute("SELECT progress, claimed FROM user_daily_progress WHERE user_id = ? AND quest_id = ?", (user_id, q_id))
                prog_row = cursor.fetchone()
                progress = prog_row[0] if prog_row else 0
                claimed = prog_row[1] if prog_row else 0
                is_done = progress >= quest['target']
                reward_text = f"🎁 {quest['reward']} 💰"
                if claimed:
                    status_btn = "✅ Получено"
                    btn_cb = "nothing"
                elif is_done:
                    status_btn = f"🎁 Забрать"
                    btn_cb = f"claim_daily_{q_id}"
                else:
                    status_btn = f"{progress}/{quest['target']}"
                    btn_cb = "nothing"
                text += f"▫️ {quest['name']}\n"
                text += f"📝 {quest['desc']}\n"
                text += f"🎁 Награда: {quest['reward']} 💰\n"
                text += f"📊 Прогресс: {progress}/{quest['target']}\n\n"
                if is_done and not claimed:
                     kb.add(types.InlineKeyboardButton(status_btn, callback_data=btn_cb))
    if not has_daily:
        text += "Загрузка заданий..."
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="back_main"))
    return kb, text

# --- МЕНЮ ДОСТИЖЕНИЙ (УРОВНИ) ---
def achievements_menu(user_id):
    user = get_user(user_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = "🏆 *ДОСТИЖЕНИЯ*\n\nПолучай награды за развитие!\n\n"
    for achv_id, achv_data in LEVEL_ACHIEVEMENTS.items():
        cursor.execute("SELECT claimed FROM user_level_achievements WHERE user_id = ? AND achv_id = ?", (user_id, achv_id))
        row = cursor.fetchone()
        claimed = row[0] if row else 0
        current_level = user[10]
        is_done = current_level >= achv_data['target']
        r_type = achv_data['reward_type']
        r_val = achv_data['reward_val']
        if r_type == 'coins':
            reward_str = f"{r_val} 💰"
        elif r_type == 'title':
            reward_str = f"Титул: {PROFILE_TITLES.get(r_val, r_val)}"
        elif r_type == 'emoji':
            reward_str = f"Эмоджи: {r_val}"
        else:
            reward_str = "???"
        if claimed:
            status_btn = "✅ Получено"
            btn_cb = "nothing"
        elif is_done:
            status_btn = f"🎁 Забрать ({reward_str})"
            btn_cb = f"claim_achv_{achv_id}"
        else:
            status_btn = f"Уровень {current_level}/{achv_data['target']}"
            btn_cb = "nothing"
        text += f"🔹 {achv_data['name']}\n"
        text += f"📝 {achv_data['desc']}\n"
        text += f"🎁 Награда: {reward_str}\n"
        text += f"📊 {status_btn}\n\n"
        if is_done and not claimed:
            kb.add(types.InlineKeyboardButton(status_btn, callback_data=btn_cb))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="back_main"))
    return kb, text

# ========== ИГРОВАЯ ЛОГИКА ==========
def start_game(chat_id, user_id, name, key, max_num, base_attempts):
    diff = get_difficulty(user_id)
    if diff == "easy":
        attempts = base_attempts + 2
        reward_mult = 1
    elif diff == "hard":
        attempts = max(1, base_attempts - 2)
        reward_mult = 2
    else:
        attempts = base_attempts
        reward_mult = 1
    secret = random.randint(1, max_num)
    save_game(user_id, name, key, secret, attempts)
    bot.send_message(chat_id, f"""{name}
🎯 Сложность: {diff.upper()}
🔢 Диапазон: 1–{max_num}
🎲 Попыток: {attempts}

Введи число:""")
    return reward_mult

def process_guess(chat_id, user_id, num, reward):
    game = get_active_game(user_id)
    secret = game[3]
    attempts = game[4] - 1
    diff = get_difficulty(user_id)
    mult = 2 if diff == "hard" else 1
    if num == secret:
        update_stats(user_id, True)
        add_coins(user_id, reward * mult)
        add_xp(user_id, reward * 2)
        delete_active_game(user_id)
        bot.send_message(chat_id, f"🎉 Победа!\nТы угадал число {secret}\n💰 +{reward * mult} монет")
        return
    if attempts <= 0:
        update_stats(user_id, False)
        delete_active_game(user_id)
        bot.send_message(chat_id, f"😢 Проигрыш\nЧисло было: {secret}")
        return
    cursor.execute("UPDATE active_games SET attempts = ? WHERE user_id = ?", (attempts, user_id))
    conn.commit()
    hint = "🔥 Горячо!" if abs(num - secret) <= 5 else "❄️ Холодно"
    bot.send_message(chat_id, f"{hint}\nОсталось попыток: {attempts}")


# Переменная режима (по умолчанию False, чтобы бот работал)
MAINTENANCE_MODE = False 

# Список разрешенных ролей
ALLOWED_ROLES = ["developer", "coder", "admin"]

def toggle_maint(signum, frame):
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    print(f"--- РЕЖИМ ТЕХРАБОТ: {MAINTENANCE_MODE} ---")

# Слушаем сигнал от Пульта
signal.signal(signal.SIGUSR1, toggle_maint)

# Функция проверки роли в БД
def has_access(user_id):
    try:
        conn = sqlite3.connect('database.db') # Проверь имя своей базы!
        cur = conn.cursor()
        # Предполагаем, что колонка называется 'role'. Если 'status' — замени.
        cur.execute("SELECT role FROM users WHERE id = ?", (user_id,))
        result = cur.fetchone()
        conn.close()
        
        if result and result[0] in ALLOWED_ROLES:
            return True
    except Exception as e:
        print(f"Ошибка БД при проверке доступа: {e}")
    return False

# ============ ОБРАБОТЧИКИ =============
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    
    # Если включен режим техработ (MAINTENANCE_MODE = True)
    # и у пользователя НЕТ доступа
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 <b>Техническое обслуживание</b>\n\nСейчас бот доступен только для разработчиков (Developer/Coder/Admin). Обычные игроки смогут зайти после завершения обновления SQL.")
        return

    # Далее идет твой обычный код...
    create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    bot.send_message(message.chat.id, "Привет 👋", reply_markup=main_menu())

@bot.message_handler(func=lambda m: is_playing(m.from_user.id) and get_active_game(m.from_user.id)[2] != "tic")
def game_input(message):
    try:
        num = int(message.text)
    except:
        return
    game = get_active_game(message.from_user.id)
    key = game[2]
    rewards = {"watermelon": 10, "sun": 15, "lemonade": 20, "beach": 25}
    process_guess(message.chat.id, message.from_user.id, num, rewards[key])

# ========== CALLBACK ==========
@bot.callback_query_handler(func=lambda c: True)
def callbacks(call):
    uid = call.from_user.id
    cid = call.message.chat.id
    create_user(uid, call.from_user.username, call.from_user.first_name)

    if call.data == "game":
        bot.edit_message_text("🎮 Выбери игру", cid, call.message.message_id, reply_markup=games_menu())
    elif call.data == 'back_main':
        bot.edit_message_text("Добро пожаловать!", cid, call.message.message_id, reply_markup=main_menu())
    elif call.data == "back":
        bot.edit_message_text("Выбирите действие:", cid, call.message.message_id, reply_markup=setting())
    elif call.data == "exit_game":
        delete_active_game(uid)
        bot.edit_message_text("🎮 Выбери игру", cid, call.message.message_id, reply_markup=games_menu())

    elif call.data == "game_watermelon":
        start_game(cid, uid, "🍉 Арбуз", "watermelon", 50, 10)
    elif call.data == "game_sun":
        start_game(cid, uid, "🌞 Солнце", "sun", 40, 8)
    elif call.data == "game_lemonade":
        start_game(cid, uid, "🍹 Лимонад", "lemonade", 20, 5)
    elif call.data == "game_beach":
        start_game(cid, uid, "🏝️ Клад", "beach", 100, 7)

    elif call.data == "prof":
        user = get_user(uid)
        status = STATUS.get(user[3], STATUS["user"])
        emoji = user[9]
        name = user[2]
        if emoji: name = f"{name} {emoji}"
        title_key = user[8]
        title = PROFILE_TITLES.get(title_key, "Без титула")
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton('🏆 Достижения', callback_data='achievements'))
        markup.add(types.InlineKeyboardButton('⬅ Назад', callback_data='back_main'))
        text = f"""👤 Имя: {name} 
🆔 ID: {user[0]}
🎭 Статус: {status}
🏷 Титул: {title}
⭐ Уровень {user[10]} 
✨ Опыт {user[11]}

💰 Монеты: {user[4]}
🎮 Игр: {user[5]}
🏆 Побед: {user[6]}
"""
        bot.edit_message_text(text, cid, call.message.message_id, reply_markup=markup)

    elif call.data == 'set':
        bot.edit_message_text("Выбирите действие:", cid, call.message.message_id, reply_markup=setting())
    elif call.data == 'level_hard':
     bot.edit_message_text('Выберите сложность', cid, call.message.message_id, reply_markup=difficulty_menu(uid))
    elif call.data == "diff_easy":
     set_difficulty(uid, "easy")
     bot.answer_callback_query(call.id, "🟢 Лёгкая сложность")
    elif call.data == "diff_normal":
     set_difficulty(uid, "normal")
     bot.answer_callback_query(call.id, "🟡 Нормальная сложность")
    elif call.data == "diff_hard":
     set_difficulty(uid, "hard")
     bot.answer_callback_query(call.id, "🔴 Хард режим")

    elif call.data == 'set_prof':
        mark = types.InlineKeyboardMarkup(row_width=1)
        mark.add(
            types.InlineKeyboardButton('✨Украшение никнейма.', callback_data='nek_prof'),
            types.InlineKeyboardButton('⬅ Назад', callback_data='back'))
        bot.edit_message_text('Выбирите действие:', cid, call.message.message_id, reply_markup=mark)

    elif call.data == 'nek_prof':
        bot.edit_message_text("😎 Выбери эмоджи для ника:", cid, call.message.message_id, reply_markup=emoji_menu(uid))
    elif call.data.startswith("emoji_"):
        emoji = call.data.replace("emoji_", "")
        cursor.execute("UPDATE users SET profile_emoji = ? WHERE id = ?", (emoji, uid))
        conn.commit()
        bot.answer_callback_query(call.id, f"{emoji} сохранено")
        bot.edit_message_reply_markup(cid, call.message.message_id, reply_markup=emoji_menu(uid))
    elif call.data == "clear":
        cursor.execute("UPDATE users SET profile_emoji = NULL WHERE id = ?", (uid,))
        conn.commit()
        bot.answer_callback_query(call.id, "Эмоджи убрано")
        bot.edit_message_reply_markup(cid, call.message.message_id, reply_markup=emoji_menu(uid))
    
    elif call.data == "shop":
        kb, text = shop_menu(uid)
        bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
    elif call.data.startswith("buy_"):
        item_key = call.data.split("_")[1]
        if item_key in SHOP_ITEMS:
            user = get_user(uid)
            price = SHOP_ITEMS[item_key]['price']
            if user[8] == item_key:
                bot.answer_callback_query(call.id, "У тебя уже есть этот титул!", show_alert=True)
            elif user[4] >= price:
                add_coins(uid, -price)
                set_title(uid, item_key)
                kb, text = shop_menu(uid)
                bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
                bot.answer_callback_query(call.id, f"🎉 Ты купил {SHOP_ITEMS[item_key]['name']}!")
            else:
                bot.answer_callback_query(call.id, f"❌ Не хватает монет! Нужно: {price}", show_alert=True)

    elif call.data == "dailies":
        kb, text = dailies_menu(uid)
        bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
    elif call.data.startswith("claim_daily_"):
        q_id = call.data.replace("claim_daily_", "")
        cursor.execute("SELECT claimed FROM user_daily_progress WHERE user_id = ? AND quest_id = ?", (uid, q_id))
        row = cursor.fetchone()
        if row is None or row[0] == 0:
            quest = next((q for q in DAILY_POOL if q['id'] == q_id), None)
            if quest:
                add_coins(uid, quest['reward'])
                cursor.execute("UPDATE user_daily_progress SET claimed = 1 WHERE user_id = ? AND quest_id = ?", (uid, q_id))
                conn.commit()
                kb, text = dailies_menu(uid)
                bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
                bot.answer_callback_query(call.id, f"🎉 +{quest['reward']} монет!")
        else:
            bot.answer_callback_query(call.id, "Уже получено!", show_alert=True)

    elif call.data == "achievements":
        kb, text = achievements_menu(uid)
        bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
    elif call.data.startswith("claim_achv_"):
        achv_id = call.data.replace("claim_achv_", "")
        cursor.execute("SELECT claimed FROM user_level_achievements WHERE user_id = ? AND achv_id = ?", (uid, achv_id))
        row = cursor.fetchone()
        claimed = row[0] if row else 0
        if claimed == 0:
            achv = LEVEL_ACHIEVEMENTS[achv_id]
            r_type = achv['reward_type']
            r_val = achv['reward_val']
            msg = ""
            if r_type == 'coins':
                add_coins(uid, r_val)
                msg = f"🎉 +{r_val} монет!"
            elif r_type == 'title':
                set_title(uid, r_val)
                msg = f"🎉 Титул '{PROFILE_TITLES.get(r_val, r_val)}' получен!"
            elif r_type == 'emoji':
                set_emoji(uid, r_val)
                msg = f"🎉 Эмоджи {r_val} получено!"
            cursor.execute("""
                INSERT INTO user_level_achievements (user_id, achv_id, claimed)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, achv_id) DO UPDATE SET claimed = 1
            """, (uid, achv_id))
            conn.commit()
            kb, text = achievements_menu(uid)
            bot.edit_message_text(text, cid, call.message.message_id, parse_mode="Markdown", reply_markup=kb)
            bot.answer_callback_query(call.id, msg)
        else:
            bot.answer_callback_query(call.id, "Уже получено!", show_alert=True)

    elif call.data == "rps":
        bot.edit_message_text("🪨 *КАМЕНЬ НОЖНИЦЫ БУМАГА*\n\nЯ буду анализировать твои ходы!", cid, call.message.message_id, parse_mode="Markdown", reply_markup=rps_keyboard())
    elif call.data == "rps_again":
        bot.edit_message_text("🪨 *КАМЕНЬ НОЖНИЦЫ БУМАГА*\n\nЯ буду анализировать твои ходы!", cid, call.message.message_id, parse_mode="Markdown", reply_markup=rps_keyboard())
        return
    elif call.data.startswith("rps_"):
        if call.data == "rps_exit":
            bot.edit_message_text("🎮 Выбери игру", cid, call.message.message_id, reply_markup=games_menu())
            return
        user_choice = int(call.data.split("_")[1])
        bot_choice = rps_predict_move(uid)
        RPS_HISTORY[uid].append(user_choice)
        if len(RPS_HISTORY[uid]) > 10: RPS_HISTORY[uid].pop(0)
        user_move_name = RPS_MOVES[user_choice]
        bot_move_name = RPS_MOVES[bot_choice]
        result_text = ""
        won = False
        if user_choice == bot_choice:
            result_text = "🤝 Ничья!"
            update_stats(uid, False)
        elif (user_choice == 0 and bot_choice == 2) or (user_choice == 1 and bot_choice == 0) or (user_choice == 2 and bot_choice == 1):
            result_text = "🎉 Ты победил!"
            won = True
            update_stats(uid, True)
            add_coins(uid, 15)
            add_xp(uid, 10)
            add_rps_win(uid)
        else:
            result_text = "🤖 Я победил!"
            update_stats(uid, False)
        res_markup = types.InlineKeyboardMarkup()
        res_markup.add(types.InlineKeyboardButton("🔄 Ещё раз", callback_data='rps_again'))
        res_markup.add(types.InlineKeyboardButton("🏠 В меню", callback_data='back_main'))
        bot.edit_message_text(f"🪨 Камень Ножницы Бумага\n\nТы: {user_move_name}\nБот: {bot_move_name}\n\n{result_text}", cid, call.message.message_id, reply_markup=res_markup)

    elif call.data == 'crest':
        tic_games[uid] = [' '] * 9
        save_game(uid, "Крестики-Нолики", "tic", 0, 0)
        bot.edit_message_text("❌ Крестики-Нолики ⭕\n\nТы играешь X\nТвой ход:", cid, call.message.message_id, reply_markup=t_keyboard(tic_games[uid]))
    elif call.data.startswith("t_"):
        if uid not in tic_games: return
        if call.data == "t_exit":
            del tic_games[uid]
            bot.edit_message_text("🎮 Выбери игру", cid, call.message.message_id, reply_markup=games_menu())
            return
        index = int(call.data.split("_")[1])
        board = tic_games[uid]
        if board[index] != ' ' or t_check_winner(board): return
        board[index] = 'X'
        result = t_check_winner(board)
        if not result:
            diff = get_difficulty(uid)
            ai_move = t_best_move(board, diff)
            if ai_move is not None: board[ai_move] = 'O'
            result = t_check_winner(board)
        if result:
            if result == 'X':
             update_stats(uid, True)
             add_coins(uid, 30)
             add_tic_win(uid) 
             text = "🎉 Ты победил!\n💰 +30 монет"
            elif result == 'O':
              update_stats(uid, False)
              text = "🤖 Я победил!"
            else:
                update_stats(uid, False)
                text = "🤝 Ничья!"
            delete_active_game(uid)
            del tic_games[uid]
            bot.edit_message_text(text, cid, call.message.message_id)
        else:
            bot.edit_message_reply_markup(cid, call.message.message_id, reply_markup=t_keyboard(board))

print("✅ Бот запущен")
bot.polling(non_stop=True)