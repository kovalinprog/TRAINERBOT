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
CREATE TABLE IF NOT EXISTS bookings (
    user_id INTEGER,
    username TEXT,
    training_id INTEGER
)
""")
conn.commit()

# ===== ТРЕНИРОВКИ =====
trainings = {}

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
    to_delete = []

    for t_id, t in trainings.items():
        dt = datetime.strptime(f"{t['date']} {t['time']}", "%d.%m.%Y %H:%M")
        if dt < now:
            to_delete.append(t_id)

    for t_id in to_delete:
        cursor.execute("DELETE FROM bookings WHERE training_id=?", (t_id,))
        trainings.pop(t_id, None)

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
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )

# ===== КНОПКИ ТРЕНИРОВОК =====
def get_trainings_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    now = datetime.now()

    for t_id, t in trainings.items():
        dt = datetime.strptime(f"{t['date']} {t['time']}", "%d.%m.%Y %H:%M")
        if dt < now:
            continue

        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]

        free = t["slots"] - count
        short_date = format_date(t["date"])

        text = f"{t['name']} {short_date} {t['time']} ({free})"

        kb.inline_keyboard.append([
            InlineKeyboardButton(text=text, callback_data=f"book_{t_id}")
        ])

    return kb

# ===== КНОПКИ УДАЛЕНИЯ =====
def get_delete_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for t_id, t in trainings.items():
        short_date = format_date(t["date"])
        text = f"{t['name']} {short_date} {t['time']}"

        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"❌ {text}", callback_data=f"delete_{t_id}")
        ])

    return kb

# ===== ОСНОВНОЙ ОБРАБОТЧИК =====
@dp.message(StateFilter(None))
async def handle(message: types.Message, state: FSMContext):

    cleanup_trainings()

    if message.text == "/start":
        await message.answer("Меню 👇", reply_markup=get_main_kb(message.from_user.id))

    elif message.text == "💪🏻 Записаться":
        now = datetime.now()
        available = False

        for t_id, t in trainings.items():
            dt = datetime.strptime(f"{t['date']} {t['time']}", "%d.%m.%Y %H:%M")
            if dt < now:
                continue

            cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
            count = cursor.fetchone()[0]

            if count < t["slots"]:
                available = True
                break

        if not available:
            await message.answer("Нет доступных тренировок 😢")
            return

        await message.answer("Выбери тренировку:", reply_markup=get_trainings_kb())

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

        for row in rows:
            t_id = row[0]

            if t_id not in trainings:
                continue

            t = trainings[t_id]
            short_date = format_date(t["date"])

            text = f"{t['name']} {short_date} {t['time']}"

            kb.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"❌ Отменить {text}",
                    callback_data=f"cancel_{t_id}"
                )
            ])

        await message.answer("Твои записи:", reply_markup=kb)

    elif message.text == "⚙️ Админка":
        if message.from_user.id != ADMIN_ID:
            return
        await message.answer("Админка", reply_markup=get_admin_kb())

    elif message.text == "⬅️ Назад":
        await message.answer("Меню", reply_markup=get_main_kb(message.from_user.id))

    elif message.text == "📋 Список участников":
        if message.from_user.id != ADMIN_ID:
            return

        text = ""

        for t_id, t in trainings.items():
            cursor.execute(
                "SELECT username FROM bookings WHERE training_id=?",
                (t_id,)
            )
            users = cursor.fetchall()

            short_date = format_date(t["date"])

            text += f"\n📌 {t['name']} {short_date} {t['time']}:\n"

            if not users:
                text += "  никто не записан\n"
            else:
                for u in users:
                    text += f"  - {u[0]}\n"

        await message.answer(text)

    elif message.text == "➕ Добавить тренировку":
        if message.from_user.id != ADMIN_ID:
            return

        await message.answer("Введите название:")
        await state.set_state(AddTraining.name)

    elif message.text == "🗑️ Удалить тренировку":
        if message.from_user.id != ADMIN_ID:
            return

        if not trainings:
            await message.answer("Нет тренировок")
            return

        await message.answer("Выбери:", reply_markup=get_delete_kb())

# ===== FSM =====
@dp.message(AddTraining.name)
async def add_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Введите дату (дд.мм.гггг):")
    await state.set_state(AddTraining.date)

@dp.message(AddTraining.date)
async def add_date(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%d.%m.%Y")
    except:
        await message.answer("Неверный формат! Пример: 25.03.2026")
        return

    await state.update_data(date=message.text)
    await message.answer("Введите время:")
    await state.set_state(AddTraining.time)

@dp.message(AddTraining.time)
async def add_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await message.answer("Введите количество мест:")
    await state.set_state(AddTraining.slots)

@dp.message(AddTraining.slots)
async def add_slots(message: types.Message, state: FSMContext):
    data = await state.get_data()

    new_id = max(trainings.keys()) + 1 if trainings else 1

    trainings[new_id] = {
        "name": data["name"],
        "date": data["date"],
        "time": data["time"],
        "slots": int(message.text)
    }

    await message.answer("Тренировка добавлена ✅", reply_markup=get_admin_kb())
    await state.clear()

# ===== CALLBACK =====
@dp.callback_query(lambda c: c.data.startswith(("book_", "cancel_", "delete_")))
async def callbacks(callback: types.CallbackQuery):

    cleanup_trainings()
    await callback.answer()

    data = callback.data
    user_id = callback.from_user.id

    if data.startswith("book_"):
        t_id = int(data.split("_")[1])

        if t_id not in trainings:
            await callback.message.answer("Тренировка не найдена ❌")
            return

        dt = datetime.strptime(f"{trainings[t_id]['date']} {trainings[t_id]['time']}", "%d.%m.%Y %H:%M")
        if dt < datetime.now():
            await callback.message.answer("Тренировка уже прошла ❌")
            return

        cursor.execute(
            "SELECT * FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        if cursor.fetchone():
            await callback.message.answer("Ты уже записан")
            return

        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]

        if count >= trainings[t_id]["slots"]:
            await callback.message.answer("Нет мест 😢")
            return

        cursor.execute(
            "INSERT INTO bookings (user_id, username, training_id) VALUES (?, ?, ?)",
            (user_id, callback.from_user.full_name, t_id)
        )
        conn.commit()

        await callback.message.answer("Ты записан ✅")

    elif data.startswith("cancel_"):
        t_id = int(data.split("_")[1])

        cursor.execute(
            "DELETE FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        conn.commit()

        await callback.message.answer("Запись отменена ❌")

    elif data.startswith("delete_"):
        if user_id != ADMIN_ID:
            return

        t_id = int(data.split("_")[1])

        if t_id not in trainings:
            await callback.message.answer("Уже удалена")
            return

        t = trainings[t_id]

        cursor.execute(
            "SELECT user_id FROM bookings WHERE training_id=?",
            (t_id,)
        )
        users = cursor.fetchall()

        short_date = format_date(t["date"])

        notify_text = (
            f"❌ Тренировка отменена\n\n"
            f"{t['name']} {short_date} {t['time']}\n\n"
            f"Извини 🙏"
        )

        for u in users:
            try:
                await bot.send_message(u[0], notify_text)
            except:
                pass

        cursor.execute("DELETE FROM bookings WHERE training_id=?", (t_id,))
        conn.commit()

        trainings.pop(t_id, None)

        await callback.message.answer("Тренировка удалена 🗑")

# ===== ЗАПУСК =====
async def main():
    print("Бот запущен 🚀")
    await dp.start_polling(bot)

asyncio.run(main())