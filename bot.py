import asyncio
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

TOKEN = "8626742579:AAFp06-KUYOzJ_e-qGDRyuWn7Gvs-mzpVoQ"
ADMIN_ID = 76038670

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== БАЗА =====
conn = sqlite3.connect("db.sqlite3", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    date TEXT,
    time TEXT,
    slots INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS waitlist (
    user_id INTEGER,
    training_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS bookings (
    user_id INTEGER,
    username TEXT,
    training_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS reminders (
    user_id INTEGER,
    training_id INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS history (
    user_id INTEGER,
    username TEXT,
    training_name TEXT,
    date TEXT,
    time TEXT
)
""")

conn.commit()

# ===== FSM =====
class AddTraining(StatesGroup):
    name = State()
    date = State()
    time = State()
    slots = State()

# ===== ДАТА =====
def format_date(date_str):
    dt = datetime.strptime(date_str, "%d.%m.%Y")
    return dt.strftime("%d.%m")

# ===== ОЧИСТКА =====
def cleanup_trainings():
    now = datetime.now()

    cursor.execute("SELECT * FROM trainings")
    trainings = cursor.fetchall()

    for t in trainings:
        t_id, name, date, time, slots = t

        dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")

        if dt < now:
            # 🔹 берём всех записанных
            cursor.execute(
                "SELECT user_id, username FROM bookings WHERE training_id=?",
                (t_id,)
            )
            users = cursor.fetchall()

            # 🔹 сохраняем в историю
            for user_id, username in users:
                cursor.execute(
                    "INSERT INTO history VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, name, date, time)
                )

            # 🔹 удаляем тренировку
            cursor.execute("DELETE FROM trainings WHERE id=?", (t_id,))
            cursor.execute("DELETE FROM bookings WHERE training_id=?", (t_id,))
            cursor.execute("DELETE FROM reminders WHERE training_id=?", (t_id,))

    conn.commit()

# ===== МЕНЮ =====
def get_main_kb(user_id):
    if user_id == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💪🏻 Записаться")],
                [KeyboardButton(text="✅ Мои записи")],
                [KeyboardButton(text="⚙️ Админка")]
            ],
            resize_keyboard=True
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💪🏻 Записаться")],
                [KeyboardButton(text="✅ Мои записи")]
            ],
            resize_keyboard=True
        )

def get_admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить тренировку")],
            [KeyboardButton(text="🗑️ Удалить тренировку")],
            [KeyboardButton(text="📋 Список участников")],
            [KeyboardButton(text="📊 Посещения")],  # ← новая кнопка
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )
# ===== СПИСОК ТРЕНИРОВОК =====
def get_trainings_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    cursor.execute("SELECT * FROM trainings")
    rows = cursor.fetchall()

    for t_id, name, date, time, slots in rows:
        try:
            dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
        except:
            continue

        if dt < datetime.now():
            continue

        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]

        free = slots - count
        short_date = format_date(date)

        name_short = name[:15] + "..." if len(name) > 15 else name
        text = f"{name_short} {short_date} {time} ({free})"

        kb.inline_keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"book_{t_id}")
        ])

    if not kb.inline_keyboard:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="Нет тренировок 😢", callback_data="empty")
        ])

    return kb

# ===== УДАЛЕНИЕ =====
def get_delete_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    cursor.execute("SELECT * FROM trainings")
    rows = cursor.fetchall()

    for t_id, name, date, time, _ in rows:
        short_date = format_date(date)

        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"❌ {name} {short_date} {time}",
                callback_data=f"delete_{t_id}"
            )
        ])

    return kb

# ===== НАПОМИНАНИЯ =====
async def reminder_loop():
    while True:
        now = datetime.now()

        cursor.execute("SELECT * FROM trainings")
        trainings_list = cursor.fetchall()

        for t_id, name, date, time, _ in trainings_list:
            try:
                training_dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
            except:
                continue

            diff = training_dt - now

            if 86340 <= diff.total_seconds() <= 86460:

                cursor.execute(
                    "SELECT user_id FROM bookings WHERE training_id=?",
                    (t_id,)
                )
                users = cursor.fetchall()

                short_date = format_date(date)

                for (user_id,) in users:

                    cursor.execute(
                        "SELECT * FROM reminders WHERE user_id=? AND training_id=?",
                        (user_id, t_id)
                    )
                    if cursor.fetchone():
                        continue

                    try:
                        await bot.send_message(
                            user_id,
                            f"⏰ Напоминание!\n\n"
                            f"Завтра тренировка 💪\n\n"
                            f"{name} {short_date} {time}\n\n"
                            f"Жду нашу тренировку! Напоминаю, чтобы ты точно не пропустила ❤️"
                        )

                        cursor.execute(
                            "INSERT INTO reminders VALUES (?, ?)",
                            (user_id, t_id)
                        )
                        conn.commit()

                    except:
                        pass

        await asyncio.sleep(60)

# ===== ОСНОВА =====
@dp.message(StateFilter(None))
async def handle(message: types.Message, state: FSMContext):

    cleanup_trainings()

    if message.text == "/start":
        await message.answer(
            "Здравствуйте! 👋
Рады приветствовать Вас в женском фитнес-пространстве 💫

Я — Ваш виртуальный ассистент. Помогу быстро и удобно записаться на тренировки к тренеру Татьяне 🤍
Она выстроит грамотный тренировочный процесс и поддержит Вас на пути к сильному, подтянутому телу и отличному самочувствию.

📌 Форматы тренировок:
• Функционально-силовые (круговые)
⏱️ Длительность: 55 минут
👯‍♀️ Только для девушек
🎯 Подходит для любого возраста и уровня подготовки
💳 Стоимость: 15,00 рублей

• Индивидуальные тренировки в тренажерном зале
📊 Программа составляется после фитнес-тестирования
💳 Стоимость и подробности — после диагностики

На занятиях Вы будете укреплять мышцы, улучшать выносливость и чувствовать больше энергии каждый день ⚡️

Хотите записаться на ближайшую тренировку или пройти фитнес-тестирование?  
Выбирай действие ниже 👇",
            reply_markup=get_main_kb(message.from_user.id)
        )

    elif message.text == "💪🏻 Записаться":
        kb = get_trainings_kb()

        if kb.inline_keyboard[0][0].text == "Нет тренировок 😢":
            await message.answer("Расписание тренировок пока не сформировано.")
            return

        await message.answer("Выбери тренировку:", reply_markup=kb)

    elif message.text == "✅ Мои записи":
        cursor.execute(
            "SELECT training_id FROM bookings WHERE user_id=?",
            (message.from_user.id,)
        )
        rows = cursor.fetchall()

        if not rows:
            await message.answer("У тебя нет записей")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[])

        for (t_id,) in rows:
            cursor.execute("SELECT * FROM trainings WHERE id=?", (t_id,))
            t = cursor.fetchone()

            if not t:
                continue

            _, name, date, time, _ = t
            short_date = format_date(date)

            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"❌ Отменить {name} {short_date} {time}",
                    callback_data=f"cancel_{t_id}"
                )
            ])

        await message.answer("Твои записи:", reply_markup=kb)

    elif message.text == "⚙️ Админка":
        if message.from_user.id != ADMIN_ID:
            return
        await message.answer("Админка", reply_markup=get_admin_kb())

    elif message.text == "⬅️ Назад":
        await message.answer("Меню 👇", reply_markup=get_main_kb(message.from_user.id))

    # ===== СПИСОК УЧАСТНИКОВ =====
    elif message.text == "📋 Список участников":
        if message.from_user.id != ADMIN_ID:
            return

        text = ""

        cursor.execute("SELECT * FROM trainings")
        trainings = cursor.fetchall()

        for t_id, name, date, time, _ in trainings:
            cursor.execute(
                "SELECT user_id, username FROM bookings WHERE training_id=?",
                (t_id,)
            )
            users = cursor.fetchall()

            short_date = format_date(date)
            text += f"\n📌 {name} {short_date} {time}:\n"

            if not users:
                text += "  никто не записан\n"
            else:
                for u in users:
                    text += f'  - <a href="tg://user?id={u[0]}">{u[1]}</a>\n'

        await message.answer(text or "Пусто", parse_mode="HTML")

    # ===== ПОСЕЩЕНИЯ =====
    elif message.text == "📊 Посещения":
        if message.from_user.id != ADMIN_ID:
            return

        cursor.execute("""
            SELECT username, COUNT(*) as visits
            FROM history
            GROUP BY user_id
            ORDER BY visits DESC
        """)
        rows = cursor.fetchall()

        if not rows:
            await message.answer("Нет данных")
            return

        text = "📊 Посещения:\n\n"

        for username, visits in rows:
            if visits == 1:
                word = "раз"
            elif 2 <= visits <= 4:
                word = "раза"
            else:
                word = "раз"

            text += f"{username} — {visits} {word}\n"

        await message.answer(text)

    # ===== ДОБАВИТЬ ТРЕНИРОВКУ =====
    elif message.text == "➕ Добавить тренировку":
        if message.from_user.id != ADMIN_ID:
            return

        await message.answer("Введите название:")
        await state.set_state(AddTraining.name)

    # ===== УДАЛИТЬ =====
    elif message.text == "🗑️ Удалить тренировку":
        if message.from_user.id != ADMIN_ID:
            return

        kb = get_delete_kb()

        if not kb.inline_keyboard:
            await message.answer("Нет тренировок")
            return

        await message.answer("Выбери:", reply_markup=kb)
# ===== FSM =====
@dp.message(AddTraining.name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите дату (дд.мм.гггг):")
    await state.set_state(AddTraining.date)

@dp.message(AddTraining.date)
async def add_date(message: types.Message, state: FSMContext):

    user_input = message.text.strip()

    try:
        parsed_date = datetime.strptime(user_input, "%d.%m.%Y")

        today = datetime.now().date()
        if parsed_date.date() < today:
            await message.answer("❌ Нельзя выбрать прошедшую дату")
            return

        clean_date = parsed_date.strftime("%d.%m.%Y")

    except:
        await message.answer(
            "❌ Неверный формат даты!\n\nВведи так: 25.12.2026"
        )
        return

    await state.update_data(date=clean_date)
    await message.answer("Введите время (например 18:30):")
    await state.set_state(AddTraining.time)

@dp.message(AddTraining.time)
async def add_time(message: types.Message, state: FSMContext):

    user_input = message.text.strip()

    try:
        parsed_time = datetime.strptime(user_input, "%H:%M")
        clean_time = parsed_time.strftime("%H:%M")
    except:
        await message.answer(
            "❌ Неверный формат времени!\n\nВведи так: 18:30"
        )
        return

    await state.update_data(time=clean_time)
    await message.answer("Введите количество мест:")
    await state.set_state(AddTraining.slots)

@dp.message(AddTraining.slots)
async def add_slots(message: types.Message, state: FSMContext):
    data = await state.get_data()

    cursor.execute(
        "INSERT INTO trainings (name, date, time, slots) VALUES (?, ?, ?, ?)",
        (data["name"], data["date"], data["time"], int(message.text))
    )
    conn.commit()

    await message.answer("Тренировка добавлена ✅", reply_markup=get_admin_kb())
    await state.clear()

# ===== CALLBACK =====

# очередь ожидания
@dp.callback_query(lambda c: c.data.startswith("wait_"))
async def waitlist_add(callback: types.CallbackQuery):

    user_id = callback.from_user.id
    t_id = int(callback.data.split("_")[1])

    cursor.execute(
        "SELECT * FROM waitlist WHERE user_id=? AND training_id=?",
        (user_id, t_id)
    )
    if cursor.fetchone():
        await callback.message.answer("Ты уже в списке ожидания ⏳")
        return

    cursor.execute(
        "INSERT INTO waitlist VALUES (?, ?)",
        (user_id, t_id)
    )
    conn.commit()

    await callback.message.answer("Ты добавлена в список ожидания ⏳")
    await callback.answer()


# основная логика
@dp.callback_query(lambda c: c.data.startswith(("book_", "cancel_", "delete_")))
async def callbacks(callback: types.CallbackQuery):

    cleanup_trainings()
    await callback.answer()

    user_id = callback.from_user.id
    data = callback.data

    # ===== ЗАПИСЬ =====
    if data.startswith("book_"):
        t_id = int(data.split("_")[1])

        cursor.execute("SELECT * FROM trainings WHERE id=?", (t_id,))
        t = cursor.fetchone()

        if not t:
            await callback.message.answer("Не найдена ❌")
            return

        _, name, date, time, slots = t

        cursor.execute(
            "SELECT * FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        if cursor.fetchone():
            await callback.message.answer("Ты уже записан")
            return

        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]

        if count >= slots:

            cursor.execute(
                "SELECT * FROM waitlist WHERE user_id=? AND training_id=?",
                (user_id, t_id)
            )

            if cursor.fetchone():
                await callback.message.answer("Ты уже в списке ожидания ⏳")
                return

            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏳ Встать в очередь", callback_data=f"wait_{t_id}")]
            ])

            await callback.message.answer(
                "Мест нет 😢\n\nХочешь, сообщу если освободится место?",
                reply_markup=kb
            )
            return

        username = callback.from_user.username
        if username:
            username = "@" + username
        else:
            username = callback.from_user.full_name

        cursor.execute(
            "INSERT INTO bookings VALUES (?, ?, ?)",
            (user_id, username, t_id)
        )
        conn.commit()

        await callback.message.answer("✅ Ты записана на тренировку! 💪")

        await callback.message.answer(
    """Реквизиты для оплаты через мобильное приложение:

ИП Кротенко Татьяна Александровна  
Адрес: Республика Беларусь, Гомельская обл., Чечерский р-н, Оторский сельсовет, п. Ковалёв Рог, ул. Крестьянская, д. 14  
УНП: 490849533  
Расчётный счёт: BY65ALFA30132G71620010270000  
в ЗАО «Альфа-Банк», БИК: ALFABY2X  

Назначение платежа:  
«Оплата за оказание услуг по Договору от 01.03.2026 г.  
Ваши ФИО (обязательно!)»

Оплату прошу вносить ежемесячно — не позднее последнего рабочего дня месяца  
(стоимость одной тренировки — 15 рублей)

После оплаты, пожалуйста, направляйте чек в личные сообщения @krotanny 📩  

Заранее благодарю! 🌺"""
)

    # ===== ОТМЕНА =====
    elif data.startswith("cancel_"):
        t_id = int(data.split("_")[1])

        cursor.execute(
            "DELETE FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        conn.commit()

        await callback.message.answer("Запись отменена ❌")

        # 🔔 уведомление очереди
        cursor.execute(
            "SELECT user_id FROM waitlist WHERE training_id=?",
            (t_id,)
        )
        wait_users = cursor.fetchall()

        for (u_id,) in wait_users:
            try:
                await bot.send_message(
                    u_id,
                    "🔥 Освободилось место на тренировку!\n\nУспей записаться 💪"
                )
            except:
                pass

        cursor.execute(
            "DELETE FROM waitlist WHERE training_id=?",
            (t_id,)
        )
        conn.commit()

    # ===== УДАЛЕНИЕ =====
    elif data.startswith("delete_"):
        if user_id != ADMIN_ID:
            return

        t_id = int(data.split("_")[1])

        cursor.execute("SELECT * FROM trainings WHERE id=?", (t_id,))
        t = cursor.fetchone()

        if not t:
            return

        _, name, date, time, _ = t
        short_date = format_date(date)

        cursor.execute(
            "SELECT user_id FROM bookings WHERE training_id=?",
            (t_id,)
        )
        users = cursor.fetchall()

        for u in users:
            try:
                await bot.send_message(
    u[0],
    f"❌ Тренировка отменена\n\n{name} {short_date} {time}\n\nИзвини 🙏"
)
            except:
                pass

        cursor.execute("DELETE FROM trainings WHERE id=?", (t_id,))
        cursor.execute("DELETE FROM bookings WHERE training_id=?", (t_id,))
        cursor.execute("DELETE FROM reminders WHERE training_id=?", (t_id,))
        cursor.execute("DELETE FROM waitlist WHERE training_id=?", (t_id,))
        conn.commit()

        await callback.message.answer("Удалено 🗑")
# ===== ЗАПУСК =====
async def main():
    print("Бот запущен 🚀")

    asyncio.create_task(reminder_loop())

    await dp.start_polling(bot)

asyncio.run(main())