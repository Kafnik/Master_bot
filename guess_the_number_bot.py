import telebot
import random
from time import sleep
from telebot import types
import configparser


bot = telebot.TeleBot('8318795699:AAF_QfgOdRzpj6LB4ZqQ1bawJG6CuefDMX4')

#---------Главные переменные--------
VERSION = '1.4'
BOT_ENABLED = True
guess_game_active = False  # Флаг активной игры
poip = 5

#User_name администраторов
ADMIN_USER = 'Kafnik'

 # Функция проверки администратора
def is_admin(message):
    message.from_user.first_name == ADMIN_USER
    return

def games_menu(message):
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton('🎄 Угадай число (1-10)', callback_data='game1')
    btn2 = types.InlineKeyboardButton('🎡 Новогоднее колесо', callback_data='game2')
    btn3 = types.InlineKeyboardButton('❄️ Снежинки', callback_data='game3')
    markup.add(btn1, btn2, btn3)
    
    bot.send_message(message.chat.id, 
                     '✨ *НОВОГОДНИЕ ИГРЫ* ✨\n\n'
                     '🎅 Выбери игру и окунись в праздничную атмосферу!',
                     parse_mode="Markdown", 
                     reply_markup=markup)

@bot.message_handler(commands=['start'])
def start(message):
    if not BOT_ENABLED and not is_admin(message):
        bot.send_message(message.chat.id, "🚫 Бот временно недоступен.")
        return
    
    msg = bot.send_message(message.chat.id, '<b>OpenbotAI</b>', parse_mode="HTML")
    sleep(1)
    
    bot.edit_message_text(
        f'Привет, {message.from_user.first_name}! 🎅\n'
        f'Добро пожаловать в новогодние игры!\n'
        f'Напиши /games чтобы увидеть игры',
        message_id=msg.message_id, 
        chat_id=message.chat.id
    )

@bot.message_handler(commands=['games'])
def games_command(message):
    if not BOT_ENABLED and not is_admin(message):
        bot.send_message(message.chat.id, "🚫 Бот временно недоступен.")
        return
    
    bot.send_message(
        message.chat.id,
        f'✨ *С НОВЫМ ГОДОМ!* ✨\n\n'
        f'🎅 Добро пожаловать в новогодние игры!',
        parse_mode="Markdown"
    )
    sleep(1)
    games_menu(message)

@bot.message_handler(commands=["enable"])
def enable_bot(message):
    global BOT_ENABLED
    if not is_admin(message):
        bot.send_message(message.chat.id, "⛔ У вас нет прав.")
    else:
        BOT_ENABLED = True
        bot.send_message(message.chat.id, "✅ Бот включён! 🎄")

@bot.message_handler(commands=["disable"])
def disable_bot(message):
    global BOT_ENABLED
    if not is_admin(message):
        bot.send_message(message.chat.id, "⛔ У вас нет прав.")
    else:
        BOT_ENABLED = False
        bot.send_message(message.chat.id, "🚫 Бот выключен для всех, кроме админов.")

@bot.callback_query_handler(func=lambda m: True)
def callback(call):
    if not BOT_ENABLED and call.from_user.first_name not in ADMIN_USER:
        bot.answer_callback_query(call.id, "🚫 Бот недоступен.")
        return

    if call.data == 'game1':
        global guess_game_active
        guess_game_active = True
        
        # Загадываем число
        secret = random.randint(1, 10)
        attempts = 5
        
        bot.send_message(
            call.message.chat.id,
            f'🎄 *ИГРА: УГАДАЙ ЧИСЛО* 🎄\n\n'
            f'🎅 Я загадал число от 1 до 10\n'
            f'❤️ У тебя {attempts} попыток\n\n'
            f'Я загадал: {secret} (только для теста!)\n'
            f'Введи число:',
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)

    elif call.data == 'game2':
        # Новогоднее колесо
        event = random.choice([
            "🎅 Встретил Деда Мороза!",
            "🎄 Украсил ёлку!",
            "❄️ Слепил снеговика!",
            "🦌 Покатался на оленях!",
            "🌟 Поймал звезду!",
            "🍪 Испеч печенье!"
        ])
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn1 = types.InlineKeyboardButton('🎡 Крутить снова', callback_data='game2')
        btn2 = types.InlineKeyboardButton('🔙 В меню', callback_data='back_menu')
        markup.add(btn1, btn2)
        
        bot.edit_message_text(
            f'🎡 *НОВОГОДНЕЕ КОЛЕСО* 🎡\n\n'
            f'🎅 Колесо крутится...\n\n'
            f'{event}\n\n'
            f'✨ Удачного дня!',
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == 'game3':
        # Игра со снежинками
        snowflakes = random.randint(5, 20)
        messages = [
            f'❄️ Ты поймал {snowflakes} снежинок!',
            f'🌟 На тебя упало {snowflakes} снежинок!',
            f'🎄 Собрал {snowflakes} снежинок с ёлки!'
        ]
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn1 = types.InlineKeyboardButton('❄️ Ловить снежинки', callback_data='game3')
        btn2 = types.InlineKeyboardButton('🔙 В меню', callback_data='back_menu')
        markup.add(btn1, btn2)
        
        bot.edit_message_text(
            f'❄️ *СНЕЖИНКИ* ❄️\n\n'
            f'{random.choice(messages)}\n\n'
            f'✨ Попробуй ещё!',
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)
    
    elif call.data == 'back_menu':
        games_menu(call.message)
        bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    global guess_game_active, poip

    if not guess_game_active:
        return
    
    try:
            guess = int(message.text)
            
            if 1 <= guess <= 10:
                # Создаем новое число для каждой попытки
                secret = random.randint(1, 10)
                
                if guess == secret:
                    congratulations = [
                        f'🎉 *УРА! Ты угадал число {secret}!* 🎉\n\n🎅 Отличная работа! С Новым Годом!',
                        f'🌟 *БРАВО! Число {secret} найдено!* 🌟\n\n🎄 Поздравляю с победой!',
                        f'🎁 *ВОТ ЭТО ДА! Ты победил!* 🎁\n\n✨ Число {secret} угадано верно!'
                    ]
                    bot.reply_to(message, random.choice(congratulations), parse_mode="Markdown")
                    guess_game_active = False
                    poip = 5
                    sleep(1)
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    btn11 = types.InlineKeyboardButton('🔢 Угадывать снова', callback_data='game1')
                    btn22 = types.InlineKeyboardButton('🔙 В меню', callback_data='back_menu')
                    markup.add(btn11, btn22)
                    bot.send_message(message.chat.id, 'Желаете сыграть еще ?', reply_markup=markup)
                else:
                    poip -= 1
                    
                    if poip == 0:
                        bot.reply_to(message, 
                                   f'❄️ *Попытки закончились* ❄️\n\nБыло загадано число: {secret}\nПопробуй ещё раз!',
                                   parse_mode="Markdown")
                        guess_game_active = False
                        poip = 5
                        sleep(1)
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        btn11 = types.InlineKeyboardButton('🔢 Угадывать снова', callback_data='game1')
                        btn22 = types.InlineKeyboardButton('🔙 В меню', callback_data='back_menu')
                        markup.add(btn11, btn22)
                        bot.send_message(message.chat.id, 'Желаете сыграть еще ?', reply_markup=markup)
                    else:
                        hint = "БОЛЬШЕ" if guess > secret else "МЕНЬШЕ"
                        bot.reply_to(message, f'🎅 Число {hint.lower()}!\n❤️ Осталось попыток: {poip}\nПопробуй ещё!')
            else:
                bot.reply_to(message, '🎅 Число должно быть от 1 до 10!')
                
    except ValueError:
        bot.reply_to(message, "🎅 Пожалуйста, введи число!")

print('❄️ Новогодний бот с игрой "Угадай число" запущен! 🎄')
bot.polling(non_stop=True)