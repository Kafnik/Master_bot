import telebot
import openbot_id
import sqlite3
import random
import time
from datetime import datetime
from telebot import types

# ======= Настройка ======
openbot_id.init_id_system()
DB_NAME = "Master_bot.1.5.db"
MAINTENANCE_MODE = False
DEVELOPER_CHAT_ID = 123456789 # Замените на свой ID
# ========================

TOKEN = 1234567890 # токен бота 

bot = telebot.TeleBot(TOKEN)

# ======= Переменные ======
ALLOWED_ROLES = ["developer", "tester", "admin", "coder"]
STATUS = {
    "developer": "🌐💠 Openbot.Ai",
    "tester": "🌐 Тестер",
    "coder": "🌐 Кодер",
    "admin": "⭐ Администратор",
    "user": "👤 Игрок",
    "banned": "🚫 Забаненный",
}

# ========== База ========
conn = sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    cursor = conn.cursor()
     
    cursor.execute("""
       CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            first_name TEXT,
            status TEXT DEFAULT 'user',
            coins INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            XP INTEGER DEFAULT 100
       )
    """)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT NULL")
    except:
        pass

    # 1. ТАБЛИЦА АКТИВНЫХ ИГР
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

    # 2. ТАБЛИЦА НЕДВИЖИМОСТИ
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_property (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            property_name TEXT,
            property_type TEXT,
            income INTEGER       
        )
    """)

    # 3. ТАБЛИЦА ИНВЕНТАРЯ (для работы команд подарков/эмодзи)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item_type TEXT,
            item_value TEXT,
            is_active INTEGER DEFAULT 0
        )
    """)

    # 4. ТАБЛИЦА АУКЦИОНА (для работы команды sell_emoji)
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
       print("✅ Колонка equipped_item успешно добавлена в таблицу users!")
    except sqlite3.OperationalError:
        # Если колонка уже существует, SQLite выдаст ошибку, которую мы просто пропускаем
        pass
    
    conn.commit()

init_db()


def get_user(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def create_user(user_id, username, first_name):
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, username, first_name, status) VALUES (?, ?, ?, 'user')",
            (user_id, username.lower() if username else None, first_name))
        conn.commit()

def add_coins(user_id, amount):
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()

# --- Функции управления активными играми ---
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

# --- Функции для игры «Бизнесмен» (Недвижимость) ---
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

# ====== Баны и доступ =====
def has_access(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result and result[0] in ALLOWED_ROLES:
        return True
    return False

def check_ban(user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT status, ban_reason FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    if result and result[0] == 'banned':
        return result[1]
    return None

# =========== Функции и механики =========
def main_mune():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton('🎮 Игры', callback_data='game'),
        types.InlineKeyboardButton('👤 Профиль', callback_data="profile"),
        types.InlineKeyboardButton('⚙ Настройки', callback_data='settings_user')
    )
    return markup

def game_mune():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('💸 Бизнесмен', callback_data='start_clicker'),
        types.InlineKeyboardButton('🔢 Угадай число', callback_data='start_number')
    )
    markup.add(
        types.InlineKeyboardButton('🎡 Летнее колесо', callback_data='start_wheel'),
        types.InlineKeyboardButton('🎲 Кубик Судьбы', callback_data='start_dice')
    )
    markup.add(
        types.InlineKeyboardButton('🔐 Взлом Кода', callback_data='start_code'),
        types.InlineKeyboardButton('💰 Торговец', callback_data='start_trader')
    )
    markup.add(
        types.InlineKeyboardButton('🔙 В главное меню', callback_data='back')
    )
    return markup

# ============ Пользователь =========
def get_user_inventory(user_id):
    cursor = conn.cursor()
    # Группируем одинаковые предметы и считаем их количество
    cursor.execute("""
        SELECT item_type, item_value, COUNT(*) 
        FROM inventory 
        WHERE user_id = ? 
        GROUP BY item_type, item_value
    """, (user_id,))
    return cursor.fetchall()

def show_inventory_ui(chat_id, message_id, user_id):
    items = get_user_inventory(user_id)
    
    cursor = conn.cursor()
    cursor.execute("SELECT equipped_item FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    equipped_item = res[0] if res and res[0] else ""

    markup = types.InlineKeyboardMarkup(row_width=1)

    if items:
        text = "🎒 <b>Твой инвентарь:</b>\nНажми на предмет ниже, чтобы надеть или снять его!\n"
        for item_type, item_value, count in items:
            if item_value == equipped_item:
                btn_text = f"✅ {item_value} (Снять)"
                btn_callback = f"unequip_{item_value}"
            else:
                btn_text = f"✨ Надеть {item_value} ({count} шт.)"
                btn_callback = f"equip_{item_value}"
            
            markup.add(types.InlineKeyboardButton(btn_text, callback_data=btn_callback))
    else:
        text = "🎒 <b>Твой инвентарь пуст!</b>"

    markup.add(types.InlineKeyboardButton('⬅️ Назад в профиль', callback_data='profile'))
    
    bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, 
                        reply_markup=markup, parse_mode="HTML")
    
# ============ Обработчики команд ==============
@bot.message_handler(commands=['start'])
def start_bot(message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username
    reason = check_ban(user_id)
    
    if MAINTENANCE_MODE and not has_access(user_id):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(user_id):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    openbot_id.create_id(user_id, first_name)
    openbot_id.register_bot_activity(user_id, "Master_bot")
    create_user(user_id, username, first_name)
    bot.send_message(message.chat.id, f"👋 Привет, {first_name}\nДобро пожаловать!", reply_markup=main_mune())

@bot.message_handler(commands=['help'])
def bot_help(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    
    text = """Доступные команды:
/start - запуск бота
/help - список команд
/sell_emoji - продать эмодзи на аукцион
/id_profile - открыть глобальный аккаунт
/game_stop - остановить игру
/bio - изменить био в глобальном аккаунте
/feedback - оставить отзыв разработчикам

Административные команды:
/ban - забанить пользователя
/unban - разбанить пользователя
/sendall - рассылка сообщения всем
/give_coins - выдать монеты
/take_coins - забрать монеты
/level_up - изменить уровень игрока
/gift - подарить предмет
/id_ban - глобальный бан ID
/id_unban - глобальный разбан ID
/id_freeze - заморозка ID
/id_unfreeze - разморозка ID
/get_status - проверить статус игрока

<i>Обновлено 2026 года</i>"""
    msg = bot.send_message(message.chat.id, 'Загрузка...')
    bot.edit_message_text(text, message_id=msg.message_id, chat_id=message.chat.id, parse_mode="HTML")

@bot.message_handler(commands=['game_stop'])
def stop_game(message):
    user_id = message.from_user.id
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

@bot.message_handler(commands=['ban'])
def ban_user(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /ban @username [причина]")
        return
    target_username = parts[1].replace("@", "").strip().lower()
    reason_text = parts[2] if len(parts) > 2 else "Без причины"
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, status FROM users WHERE username = ?", (target_username,))
    result = cursor.fetchone()
    if result:
        target_id, current_status = result[0], result[1]
        if current_status in ALLOWED_ROLES:
            bot.reply_to(message, f"⛔ Нельзя забанить {STATUS.get(current_status, current_status)}!")
        elif current_status == 'banned':
            bot.reply_to(message, f"⚠️ @{target_username} уже забанен.")
        else:
            cursor.execute("UPDATE users SET status = 'banned', ban_reason = ? WHERE user_id = ?", (reason_text, target_id))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ @{target_username} забанен!\nПричина: {reason_text}")
            try:
                bot.send_message(target_id, f"🚫 Вы заблокированы!\nПричина: {reason_text}")
            except:
                pass
    else:
        bot.reply_to(message, f"❌ Пользователь @{target_username} не найден.")

@bot.message_handler(commands=['unban'])
def unban_by_username(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /unban @username")
        return
    target_username = parts[1].replace("@", "").strip().lower()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, status FROM users WHERE username = ?", (target_username,))
    result = cursor.fetchone()
    if result:
        target_id, current_status = result[0], result[1]
        if current_status != 'banned':
            bot.reply_to(message, f"❓ Пользователь @{target_username} не забанен.")
        else:
            cursor.execute("UPDATE users SET status = 'user', ban_reason = NULL WHERE user_id = ?", (target_id,))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ Пользователь @{target_username} разбанен!")
            try:
                bot.send_message(target_id, "🔓 Ваш доступ восстановлен!")
            except:
                pass
    else:
        bot.reply_to(message, f"❌ Пользователь @{target_username} не найден.")

@bot.message_handler(commands=['sendall'])
def send_all(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    
    text = message.text.replace("/sendall", "").strip()
    if not text:
        bot.reply_to(message, "❌ Введи текст для рассылки.\nПример: `/sendall Привет!`", parse_mode="Markdown")
        return
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    bot.send_message(message.chat.id, f"🚀 Рассылка запущена...\nВсего: {len(users)}")
    success = blocked = errors = 0
    for user in users:
        try:
            bot.send_message(user[0], text, parse_mode="HTML")
            success += 1
        except telebot.apihelper.ApiTelegramException as e:
            if e.description == "Forbidden: bot was blocked by the user":
                blocked += 1
            else:
                errors += 1
        except:
            errors += 1
    bot.send_message(message.chat.id, f"✅ Готово!\n👤 Успешно: {success}\n🚫 Заблокировали: {blocked}\n⚠️ Ошибки: {errors}")

@bot.message_handler(commands=['sell_emoji'])
def sell_item(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
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
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM inventory WHERE user_id = ? AND item_value = ?", (uid, emoji))
    if not cursor.fetchone():
        bot.reply_to(message, "❌ У тебя нет такого эмодзи в инвентаре!")
        return
    cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_value = ?", (uid, emoji))
    cursor.execute("INSERT INTO auction (seller_id, item_type, item_value, price) VALUES (?, 'emoji', ?, ?)", (uid, emoji, price))
    conn.commit()
    bot.reply_to(message, f"✅ Твой лот {emoji} выставлен на аукцион за {price} 💰")

@bot.message_handler(commands=['gift'])
def gift_item(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
        
    parts = message.text.split()
    if len(parts) < 3:
        bot.send_message(message.chat.id, "⚠️ Неверный формат! Пиши так:\n`/gift @юзернейм эмодзи`", parse_mode="Markdown")
        return
        
    target_uname = parts[1].replace("@", "").lower()
    item = parts[2]
    
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
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
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
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
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

@bot.message_handler(commands=['id_ban'])
def id_ban(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(message.from_user.id):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /id_ban @username")
        return
    username = parts[1].replace("@", "").lower()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    data = openbot_id.get_id(res[0])
    if not data:
        bot.reply_to(message, f"❌ У @{username} нет OpenbotAI ID.")
        return
    if data[3] == "banned":
        bot.reply_to(message, f"⚠️ @{username} уже забанен в ID!")
        return
    openbot_id.set_status(res[0], "banned")
    bot.reply_to(message, f"☠️ ID пользователя @{username} забанен!")
    try:
        bot.send_message(res[0], "☠️ Ваш OpenbotAI ID был забанен!")
    except:
        pass

@bot.message_handler(commands=['id_unban'])
def id_unban(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(message.from_user.id):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /id_unban @username")
        return
    username = parts[1].replace("@", "").lower()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    data = openbot_id.get_id(res[0])
    if not data:
        bot.reply_to(message, f"❌ У @{username} нет OpenbotAI ID.")
        return
    if data[3] != "banned":
        bot.reply_to(message, f"⚠️ @{username} не забанен в ID!")
        return
    openbot_id.set_status(res[0], "user")
    bot.reply_to(message, f"✅ ID пользователя @{username} разбанен!")
    try:
        bot.send_message(res[0], "✅ Ваш OpenbotAI ID был разбанен!")
    except:
        pass

@bot.message_handler(commands=['id_freeze'])
def id_freeze(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(message.from_user.id):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /id_freeze @username")
        return
    username = parts[1].replace("@", "").lower()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    data = openbot_id.get_id(res[0])
    if not data:
        bot.reply_to(message, f"❌ У @{username} нет OpenbotAI ID.")
        return
    if data[3] == "frozen":
        bot.reply_to(message, f"⚠️ @{username} уже заморожен!")
        return
    if data[3] == "banned":
        bot.reply_to(message, f"⚠️ @{username} забанен, сначала разбань!")
        return
    openbot_id.set_status(res[0], "frozen")
    bot.reply_to(message, f"❄️ ID пользователя @{username} заморожен!")
    try:
        bot.send_message(res[0], "❄️ Ваш OpenbotAI ID был заморожен!")
    except:
        pass

@bot.message_handler(commands=['id_unfreeze'])
def id_unfreeze(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(message.from_user.id):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Формат: /id_unfreeze @username")
        return
    username = parts[1].replace("@", "").lower()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    res = cursor.fetchone()
    if not res:
        bot.reply_to(message, f"❌ Пользователь @{username} не найден.")
        return
    data = openbot_id.get_id(res[0])
    if not data:
        bot.reply_to(message, f"❌ У @{username} нет OpenbotAI ID.")
        return
    if data[3] != "frozen":
        bot.reply_to(message, f"⚠️ @{username} не заморожен!")
        return
    openbot_id.set_status(res[0], "user")
    bot.reply_to(message, f"✅ ID пользователя @{username} разморожен!")
    try:
        bot.send_message(res[0], "✅ Ваш OpenbotAI ID был разморожен!")
    except:
        pass

@bot.message_handler(commands=['level_up'])
def cmd_level_up(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
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
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
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
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
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
    
    openbot_id.update_bio(uid, new_bio)
    bot.reply_to(message, f"✅ Био обновлено:\n`{new_bio}`", parse_mode='Markdown')

@bot.message_handler(commands=['id_profile'])
def id_profile(message):
    uid, cid = message.from_user.id, message.chat.id
    global_data = openbot_id.get_id(uid)
    reason = check_ban(uid)

    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return

    if global_data:
        g_name = global_data[2]
        g_tag = global_data[5] or "Не установлен"
        g_bio = global_data[8] or "Не установлена"
        g_data = global_data[7]
        
        bots_list = openbot_id.get_active_bots(uid)
        g_active_bot = ", ".join(bots_list) if bots_list else "Ни в каких"
        g_status = openbot_id.STATUS.get(global_data[6], "👤 Игрок")
            
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

@bot.message_handler(commands=['get_status'])
def set_status_command(message):
    uid = message.from_user.id
    reason = check_ban(uid)
    
    if MAINTENANCE_MODE and not has_access(uid):
        bot.reply_to(message, "🟠 Ведутся технические работы. Зайдите позже!")
        return
    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return

    parts = message.text.split()
    if len(parts) < 3:
        bot.reply_to(message, "⚠️ Формат: `/get_status @username developer`\n\nДоступные роли: `developer`, `tester`, `admin`, `coder`, `user`", parse_mode="Markdown")
        return

    target_username = parts[1].replace("@", "").strip().lower()
    new_status = parts[2].lower()

    if new_status not in STATUS and new_status != "user":
        bot.reply_to(message, "❌ Неизвестный статус!\nДоступные: `developer`, `tester`, `admin`, `coder`, `user`", parse_mode="Markdown")
        return

    cursor = conn.cursor()
    cursor.execute("SELECT user_id, first_name FROM users WHERE username = ?", (target_username,))
    res = cursor.fetchone()

    if not res:
        bot.reply_to(message, f"❌ Пользователь @{target_username} не найден.")
        return

    target_id, target_name = res
    cursor.execute("UPDATE users SET status = ? WHERE user_id = ?", (new_status, target_id))
    conn.commit()

    status_display = STATUS.get(new_status, new_status)
    bot.reply_to(message, f"✅ Статус пользователя @{target_username} успешно изменен на: **{status_display}**", parse_mode="Markdown")
    try:
        bot.send_message(target_id, f"🎭 Ваш статус был изменен на: **{status_display}**", parse_mode="Markdown")
    except:
        pass

@bot.message_handler(commands=['MODE_false'])
def mode_false(message):
    uid = message.from_user.id
    reason = check_ban(uid)

    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = False
    bot.reply_to(message, "🟢 Режим обслуживания отключен.")

@bot.message_handler(commands=['MODE_true'])
def mode_tre(message):
    uid = message.from_user.id
    reason = check_ban(uid)

    if openbot_id.is_globally_banned(uid):
        bot.reply_to(message, "☠ Доступ закрыт!\nВаш глобальный аккаунт заблокирован во всех ботах нашей сети.")
        return
    if reason:
        bot.reply_to(message, f"🚫 Вы заблокированы!\nПричина: {reason}")
        return
    
    if not has_access(uid):
        bot.reply_to(message, "⛔ У вас нет прав.")
        return
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = True
    bot.reply_to(message, "🟠 Режим обслуживания включен.")

# =========== Обработчик кнопок =============
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    uid = call.from_user.id
    cid = call.message.chat.id
    
    if call.data == 'game':
        bot.edit_message_text(
            "🎮 <b>Выберите игру:</b>",
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=game_mune(),
            parse_mode="HTML"
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == 'back':
        bot.edit_message_text(
            f'👋 Привет, {call.from_user.first_name}\nДобро пожаловать!',
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=main_mune()
        )
        bot.answer_callback_query(call.id)

    elif call.data == 'profile':
        user = get_user(user_id)
        if user:
            name = user[2] if user[2] else "Игрок"
            # Если предмет надет, берем его, иначе пустая строка
            equipped = user[12] if len(user) > 12 and user[12] else ""
            
            # Склеиваем имя и эмодзи без пробелов (получится kafnik🎈)
            full_name = f"{equipped} {name}"

            profile_text = f"""
👤 <b>Твой Профиль</b>
Имя: <b>{full_name}</b>
Статус: {STATUS.get(user[3], 'Неизвестно')}
💰 Монеты: {user[4]}
⭐ Уровень: {user[5]}
✨ XP: {user[6]}/100
            """
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton('🎒 Инвентарь', callback_data='open_inventory'),
                types.InlineKeyboardButton('⚙ Настройки ID', callback_data='settings_id'),
                types.InlineKeyboardButton('🔙 В главное меню', callback_data='back')
            )
            bot.edit_message_text(profile_text, chat_id=user_id, message_id=call.message.message_id, 
                                reply_markup=markup, parse_mode="HTML")
        bot.answer_callback_query(call.id)
    
    elif call.data == 'open_inventory':
        show_inventory_ui(call.message.chat.id, call.message.message_id, user_id)
        bot.answer_callback_query(call.id)

    # --- НАДЕТЬ ПРЕДМЕТ ---
    elif call.data.startswith('equip_'):
        item_to_equip = call.data.replace('equip_', '')
        
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM inventory WHERE user_id = ? AND item_value = ?", (user_id, item_to_equip))
        if cursor.fetchone():
            cursor.execute("UPDATE users SET equipped_item = ? WHERE user_id = ?", (item_to_equip, user_id))
            conn.commit()
            bot.answer_callback_query(call.id, f"✅ Ты надел {item_to_equip}!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ У тебя больше нет этого предмета!", show_alert=True)
            
        # Обновляем кнопки инвентаря
        show_inventory_ui(call.message.chat.id, call.message.message_id, user_id)

    # --- СНЯТЬ ПРЕДМЕТ ---
    elif call.data.startswith('unequip_'):
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET equipped_item = '' WHERE user_id = ?", (user_id,))
        conn.commit()
        
        bot.answer_callback_query(call.id, "ℹ️ Значок снят!", show_alert=True)
        
        # Обновляем кнопки инвентаря
        show_inventory_ui(call.message.chat.id, call.message.message_id, user_id)
        
    # ===== ИГРА: БИЗНЕСМЕН =====
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
            f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {user[4]} монет 💰\nДля выхода введи /game_stop", 
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
        bot.edit_message_text(
            f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {updated_user[4]} монет 💰\nДля выхода введи /game_stop", 
            chat_id=user_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML"
        )
        bot.answer_callback_query(call.id, "Заработано +10 монет!")
        
    elif call.data == 'settings_user':
        bot.answer_callback_query(call.id, '⚙ Еще в разработке !')
        
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
            f"💸 <b>Игра «Бизнесмен»</b>\n\nТвой баланс: {user[4]} монет 💰\nДля выхода введи /game_stop", 
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
    
    # ===== ИГРА: ЛЕТНЕЕ КОЛЕСО =====
    elif call.data == 'start_wheel':
        set_active_game(user_id, call.message.chat.id, 'wheel')
        bot.edit_message_text('🎡 Колесо начинает крутиться...', chat_id=user_id, message_id=call.message.message_id)
        time.sleep(2)
        
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
            f"🎡 <b>ЛЕТНЕЕ КОЛЕСО УДАЧИ</b>\n\n{event['text']}", 
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
        global_data = openbot_id.get_id(uid)
        
        if global_data:
            g_name = global_data[2]
            g_tag = global_data[5] or "Не установлен"
            g_bio = global_data[8] or "Не установлена"
            g_data = global_data[7]
            g_active_bot = openbot_id.get_active_bots(uid)
            g_status = openbot_id.STATUS.get(global_data[6], "👤 Игрок")
            
            text = f"""**🌐 Общий профиль Openbot AI ID**

Вы можете изменить свои глобальные данные. Они обновятся во всех ботах нашей сети!

**🏷 Текущее имя:** `{g_name}`
**🆔 Ваш Тег:** `{g_tag}`
**🎭 Глобальный статус:** `{g_status}`
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
    
    # Обработчик продажи товара
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


@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    user_id = message.from_user.id
    game_data = get_active_game(user_id)
    
    if not game_data:
        return
    
    game_type = game_data[0]
    
    # ===== ОБРАБОТЧИК: УГАДАЙ ЧИСЛО =====
    if game_type == 'number':
        secret = game_data[1]
        attempts = game_data[2]
        
        try:
            guess = int(message.text)
            
            if 1 <= guess <= 10:
                if guess == secret:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET coins = coins + 50 WHERE user_id = ?", (user_id,))
                    conn.commit()
                    
                    bot.reply_to(message, f'🎉 *УРА! Ты угадал число {secret}!* 🎉\n\n✨ Награда: +50 монет!', parse_mode="Markdown")
                    delete_active_game(user_id)
                    
                    time.sleep(1)
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        types.InlineKeyboardButton('🔢 Угадывать снова', callback_data='start_number'),
                        types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                    )
                    bot.send_message(message.chat.id, 'Желаете сыграть еще?', reply_markup=markup)
                else:
                    attempts -= 1
                    
                    if attempts == 0:
                        bot.reply_to(message, f'☠ *Попытки закончились* ☠\n\nБыло загадано число: {secret}', parse_mode="Markdown")
                        delete_active_game(user_id)
                        
                        time.sleep(1)
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(
                            types.InlineKeyboardButton('🔢 Угадывать снова', callback_data='start_number'),
                            types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                        )
                        bot.send_message(message.chat.id, 'Желаете сыграть еще?', reply_markup=markup)
                    else:
                        update_game_attempts(user_id, attempts)
                        hint = "МЕНЬШЕ" if guess > secret else "БОЛЬШЕ"
                        bot.reply_to(message, f'🔢 Не угадал! Моё число {hint.lower()}.\n❤️ Осталось: {attempts}\nПопробуй ещё!')
            else:
                bot.reply_to(message, '🔢 Число должно быть от 1 до 10!')
                    
        except ValueError:
            bot.reply_to(message, "🔢 Пожалуйста, введи число!")
    
    # ===== ОБРАБОТЧИК: КУБИК СУДЬБЫ =====
    elif game_type == 'dice':
        secret = game_data[1]
        
        try:
            guess = int(message.text)
            
            if 1 <= guess <= 6:
                if guess == secret:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET coins = coins + 100, XP = XP + 25 WHERE user_id = ?", (user_id,))
                    conn.commit()
                    
                    bot.reply_to(message, f'🎲 *ОТЛИЧНЫЙ БРОСОК!* 🎲\n\nТы угадал число {secret}!\n\n🎉 Награда: +100 монет + 25 XP!', parse_mode="Markdown")
                    delete_active_game(user_id)
                    
                    time.sleep(1)
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        types.InlineKeyboardButton('🎲 Бросить снова', callback_data='start_dice'),
                        types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                    )
                    bot.send_message(message.chat.id, 'Попробуешь еще?', reply_markup=markup)
                else:
                    bot.reply_to(message, f'❌ Неудача! Было число {secret}\n\nМожешь попробовать еще раз!', parse_mode="Markdown")
                    delete_active_game(user_id)
                    
                    time.sleep(1)
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        types.InlineKeyboardButton('🎲 Бросить снова', callback_data='start_dice'),
                        types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                    )
                    bot.send_message(message.chat.id, 'Хочешь еще раз попробовать?', reply_markup=markup)
            else:
                bot.reply_to(message, '🎲 Число должно быть от 1 до 6!')
                    
        except ValueError:
            bot.reply_to(message, "🎲 Пожалуйста, введи число от 1 до 6!")
    
    # ===== ОБРАБОТЧИК: ВЗЛОМ КОДА =====
    elif game_type == 'code':
        secret_code = game_data[3]
        attempts = game_data[2]
        
        guess = message.text.strip()
        
        if len(guess) == 4 and guess.isdigit():
            if guess == secret_code:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET coins = coins + 150, XP = XP + 50 WHERE user_id = ?", (user_id,))
                conn.commit()
                
                bot.reply_to(message, f'🎉 *КОД ВЗЛОМАН!* 🎉\n\nПравильный код: {secret_code}\n\n💰 Награда: +150 монет + 50 XP!', parse_mode="Markdown")
                delete_active_game(user_id)
                
                time.sleep(1)
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton('🔐 Взломать еще', callback_data='start_code'),
                    types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                )
                bot.send_message(message.chat.id, 'Хочешь еще один код взломать?', reply_markup=markup)
            else:
                matched = count_matched_digits(secret_code, guess)
                attempts -= 1
                update_game_attempts(user_id, attempts)
                
                if attempts == 0:
                    bot.reply_to(message, f'☠ *КОД НЕ ВЗЛОМАН* ☠\n\nПравильный код: {secret_code}', parse_mode="Markdown")
                    delete_active_game(user_id)
                    
                    time.sleep(1)
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        types.InlineKeyboardButton('🔐 Попробовать снова', callback_data='start_code'),
                        types.InlineKeyboardButton('🔙 В меню', callback_data='game')
                    )
                    bot.send_message(message.chat.id, 'Хочешь еще раз?', reply_markup=markup)
                else:
                    bot.reply_to(message, f'❌ Неправильно!\n\n💡 Подсказка: {matched} цифр(ы) совпадают\n\n❤️ Осталось попыток: {attempts}\n\nПопробуй еще!')
        else:
            bot.reply_to(message, '🔐 Пожалуйста, введи 4-значный код (например: 1234)')

print("[ Успешно ] Бот запущен и готов к работе!")
bot.polling(none_stop=True, timeout=70)