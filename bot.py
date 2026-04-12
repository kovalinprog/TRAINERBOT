import asyncio
import json
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from zoneinfo import ZoneInfo

TOKEN = os.getenv("BOT_TOKEN", "8626742579:AAFp06-KUYOzJ_e-qGDRyuWn7Gvs-mzpVoQ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "76038670"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "16DnKUsc2KZ5foX9jf4RoNU5VBvqqPVM_XPVqnFvyFhU")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "trainerbot-492814-fc3ec93852f5.json")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Europe/Minsk")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
VISITS_SHEET_NAME = "Посещения"
MEASUREMENTS_SHEET_NAME = "Замеры"
COMPLETED_TRAININGS_SHEET_NAME = "Прошедшие тренировки"
VISITS_HEADERS = ["Ник", "Посещений"]
MEASUREMENTS_HEADERS = ["Ник", "Дата", "Вес"]
COMPLETED_TRAININGS_HEADERS = ["Дата", "Время", "Тренировка", "Присутствовали"]

try:
    APP_TZ = ZoneInfo(APP_TIMEZONE)
except Exception:
    print(f"Invalid APP_TIMEZONE '{APP_TIMEZONE}', fallback to UTC")
    APP_TZ = ZoneInfo("UTC")

bot = Bot(token=TOKEN)
dp = Dispatcher()
sheets_service = None
sheets_initialized = False

# ===== БАЗА =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(BASE_DIR, "db.sqlite3")

conn = sqlite3.connect(db_path, check_same_thread=False)
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

cursor.execute("""
CREATE TABLE IF NOT EXISTS measurements (
    user_id INTEGER,
    username TEXT,
    date TEXT,
    weight REAL
)
""")

conn.commit()

# ===== FSM =====
class AddTraining(StatesGroup):
    name = State()
    date = State()
    time = State()
    slots = State()

class AddMeasurement(StatesGroup):
    date = State()
    weight = State()

def now_local():
    return datetime.now(APP_TZ)

def get_sheets_service():
    global sheets_service

    if sheets_service is not None:
        return sheets_service

    credentials_path = os.path.join(os.path.dirname(__file__), GOOGLE_CREDENTIALS_FILE)

    try:
        credentials_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if credentials_json:
            credentials = Credentials.from_service_account_info(
                json.loads(credentials_json),
                scopes=GOOGLE_SCOPES
            )
        elif os.path.exists(credentials_path):
            credentials = Credentials.from_service_account_file(
                credentials_path,
                scopes=GOOGLE_SCOPES
            )
        else:
            print(
                "Google Sheets credentials not found. "
                f"Set GOOGLE_CREDENTIALS_JSON or add file: {credentials_path}"
            )
            return None
        sheets_service = build("sheets", "v4", credentials=credentials)
        return sheets_service
    except Exception as e:
        print(f"Google Sheets init error: {e}")
        return None

def ensure_sheet_exists(service, sheet_name, headers):
    sheet_range = f"'{sheet_name}'!A1"
    header_range = f"'{sheet_name}'!A1:Z1"

    spreadsheet = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = {
        sheet["properties"]["title"]
        for sheet in spreadsheet.get("sheets", [])
    }

    if sheet_name not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={
                "requests": [{
                    "addSheet": {
                        "properties": {"title": sheet_name}
                    }
                }]
            }
        ).execute()

    existing = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=header_range
    ).execute()

    if headers and not existing.get("values"):
        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=sheet_range,
            valueInputOption="RAW",
            body={"values": [headers]}
        ).execute()

def init_google_sheets():
    global sheets_initialized

    if sheets_initialized:
        return True

    service = get_sheets_service()
    if service is None:
        return False

    try:
        ensure_sheet_exists(service, VISITS_SHEET_NAME, VISITS_HEADERS)
        ensure_sheet_exists(service, COMPLETED_TRAININGS_SHEET_NAME, COMPLETED_TRAININGS_HEADERS)
        ensure_sheet_exists(service, MEASUREMENTS_SHEET_NAME, MEASUREMENTS_HEADERS)
        sheets_initialized = True
        return True
    except HttpError as e:
        print(f"Google Sheets setup error: {e}")
        return False
    except Exception as e:
        print(f"Google Sheets unexpected setup error: {e}")
        return False

def get_column_letter(column_number):
    result = ""
    while column_number > 0:
        column_number, remainder = divmod(column_number - 1, 26)
        result = chr(65 + remainder) + result
    return result

def get_column_index(column_letter):
    index = 0
    for char in column_letter:
        index = index * 26 + (ord(char.upper()) - 64)
    return index

def parse_a1_range_end_column(updated_range):
    last_part = updated_range.split(":")[-1]
    letters = "".join(ch for ch in last_part if ch.isalpha())
    return get_column_index(letters) if letters else 1

def append_google_row(sheet_name, row):
    if not init_google_sheets():
        return False

    sheet_range = f"'{sheet_name}'!A1"

    try:
        get_sheets_service().spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=sheet_range,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()
        return True
    except HttpError as e:
        print(f"Google Sheets append error ({sheet_name}): {e}")
        return False
    except Exception as e:
        print(f"Google Sheets unexpected append error ({sheet_name}): {e}")
        return False

def upsert_visit_count(username):
    if not init_google_sheets():
        return False

    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{VISITS_SHEET_NAME}'!A:B"
        ).execute()
        values = result.get("values", [])

        if not values:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{VISITS_SHEET_NAME}'!A1:B1",
                valueInputOption="RAW",
                body={"values": [VISITS_HEADERS]}
            ).execute()
            values = [VISITS_HEADERS]
        elif values[0][:len(VISITS_HEADERS)] != VISITS_HEADERS:
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{VISITS_SHEET_NAME}'!A1:B1",
                valueInputOption="RAW",
                body={"values": [VISITS_HEADERS]}
            ).execute()
            values[0] = VISITS_HEADERS

        target_row_index = None
        current_count = 0
        for index, row in enumerate(values[1:], start=2):
            if row and row[0] == username:
                target_row_index = index
                if len(row) > 1:
                    try:
                        current_count = int(float(row[1]))
                    except:
                        current_count = 0
                break

        if target_row_index is None:
            service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{VISITS_SHEET_NAME}'!A1",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [[username, 1]]}
            ).execute()
            return True

        service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"'{VISITS_SHEET_NAME}'!A{target_row_index}:B{target_row_index}",
            valueInputOption="USER_ENTERED",
            body={"values": [[username, current_count + 1]]}
        ).execute()
        return True
    except HttpError as e:
        print(f"Google Sheets visit upsert error: {e}")
        return False
    except Exception as e:
        print(f"Google Sheets unexpected visit upsert error: {e}")
        return False

def append_measurement_row(username, date, weight):
    return append_google_row(
        MEASUREMENTS_SHEET_NAME,
        [username, date, weight]
    )

def append_completed_training(date, time, training_name, attendees):
    attendees_text = ", ".join(attendees) if attendees else "Никто"
    return append_google_row(
        COMPLETED_TRAININGS_SHEET_NAME,
        [date, time, training_name, attendees_text]
    )

def get_training_display(name, date, time):
    return f"{name} {format_date(date)} {time}"

def get_active_trainings():
    cursor.execute("SELECT * FROM trainings")
    rows = []
    for training in cursor.fetchall():
        t_id, name, date, time, slots = training
        try:
            dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
        except:
            continue
        if dt.replace(tzinfo=APP_TZ) < now_local():
            continue
        rows.append(training)
    return rows

def get_manage_bookings_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for t_id, name, date, time, _ in get_active_trainings():
        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]
        if count == 0:
            continue

        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{get_training_display(name, date, time)} ({count})",
                callback_data=f"admin_manage_training_{t_id}"
            )
        ])

    if not kb.inline_keyboard:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text="Нет записей", callback_data="empty")
        ])

    return kb

def get_training_users_kb(training_id):
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    cursor.execute(
        "SELECT user_id, username FROM bookings WHERE training_id=? ORDER BY username COLLATE NOCASE",
        (training_id,)
    )
    users = cursor.fetchall()

    for booked_user_id, username in users:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=username,
                callback_data=f"admin_manage_user_{training_id}_{booked_user_id}"
            )
        ])

    kb.inline_keyboard.append([
        InlineKeyboardButton(text="⬅️ К тренировкам", callback_data="admin_manage_back")
    ])
    return kb

def get_manage_user_action_kb(training_id, booked_user_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="❌ Отменить запись",
                callback_data=f"admin_cancel_booking_{training_id}_{booked_user_id}"
            )],
            [InlineKeyboardButton(
                text="🔁 Перенести",
                callback_data=f"admin_move_booking_{training_id}_{booked_user_id}"
            )],
            [InlineKeyboardButton(
                text="⬅️ К участникам",
                callback_data=f"admin_manage_training_{training_id}"
            )]
        ]
    )

def get_move_target_kb(from_training_id, booked_user_id):
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    cursor.execute("SELECT username FROM bookings WHERE user_id=? AND training_id=?", (booked_user_id, from_training_id))
    row = cursor.fetchone()
    username = row[0] if row else "Участник"

    for t_id, name, date, time, slots in get_active_trainings():
        if t_id == from_training_id:
            continue

        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]
        if count >= slots:
            continue

        cursor.execute("SELECT 1 FROM bookings WHERE user_id=? AND training_id=?", (booked_user_id, t_id))
        if cursor.fetchone():
            continue

        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"{get_training_display(name, date, time)} ({slots - count})",
                callback_data=f"admin_move_to_{from_training_id}_{booked_user_id}_{t_id}"
            )
        ])

    if not kb.inline_keyboard:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"Нет вариантов для {username}", callback_data="empty")
        ])

    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="⬅️ К действиям",
            callback_data=f"admin_manage_user_{from_training_id}_{booked_user_id}"
        )
    ])
    return kb

async def notify_waitlist(training_id):
    cursor.execute("SELECT user_id FROM waitlist WHERE training_id=?", (training_id,))
    wait_users = cursor.fetchall()

    for (wait_user_id,) in wait_users:
        try:
            await bot.send_message(
                wait_user_id,
                "🔥 Освободилось место на тренировку!\n\nУспей записаться 💪"
            )
        except:
            pass

    cursor.execute("DELETE FROM waitlist WHERE training_id=?", (training_id,))
    conn.commit()

# ===== ДАТА =====
def format_date(date_str):
    dt = datetime.strptime(date_str, "%d.%m.%Y")
    return dt.strftime("%d.%m")

def cleanup_trainings():
    now = now_local()
    print(f"Проверка очистки: {now}")

    cursor.execute("SELECT * FROM trainings")
    trainings = cursor.fetchall()

    for t in trainings:
        t_id, name, date, time, slots = t

        try:
            dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
        except Exception as e:
            print(f"Ошибка парсинга даты для тренировки {t_id}: {e}")
            continue

        if dt.replace(tzinfo=APP_TZ) < now:
            print(f"Удаляем тренировку: {name} {date} {time}")
            # 🔹 берём всех записанных
            cursor.execute("SELECT user_id, username FROM bookings WHERE training_id=?", (t_id,))
            users = cursor.fetchall()
            attendees = []

            for user_id, username in users:
                attendees.append(username)
                cursor.execute(
                    "INSERT INTO history VALUES (?, ?, ?, ?, ?)",
                    (user_id, username, name, date, time)
                )
                upsert_visit_count(username)

            append_completed_training(date, time, name, attendees)

            # удаляем
            cursor.execute("DELETE FROM trainings WHERE id=?", (t_id,))
            cursor.execute("DELETE FROM bookings WHERE training_id=?", (t_id,))
            cursor.execute("DELETE FROM reminders WHERE training_id=?", (t_id,))
            cursor.execute("DELETE FROM waitlist WHERE training_id=?", (t_id,))

    conn.commit()
# ===== МЕНЮ =====
def get_main_kb(user_id):
    if user_id == ADMIN_ID:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💪🏻 Записаться")],
                [KeyboardButton(text="✅ Мои записи")],
                [KeyboardButton(text="📏 Добавить замеры")],
                [KeyboardButton(text="⚙️ Админка")]
            ],
            resize_keyboard=True
        )
    else:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="💪🏻 Записаться")],
                [KeyboardButton(text="✅ Мои записи")],
                [KeyboardButton(text="📏 Добавить замеры")]  # ← для клиента
            ],
            resize_keyboard=True
        )

def get_admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить тренировку")],
            [KeyboardButton(text="🗑️ Удалить тренировку")],
            [KeyboardButton(text="🔁 Управление записями")],
            [KeyboardButton(text="📋 Список участников")],
            [KeyboardButton(text="📊 Посещения")],
            [KeyboardButton(text="📈 Отчет веса")],  # ← для админа
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )

def get_measurement_cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⬅️ Назад")]
        ],
        resize_keyboard=True
    )

def get_clear_list_kb(action):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Очистить", callback_data=action)]
        ]
    )

# ===== РЎРџРРЎРћРљ РўР Р•РќРР РћР’РћРљ =====
def get_trainings_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    cursor.execute("SELECT * FROM trainings")
    rows = cursor.fetchall()

    for t_id, name, date, time, slots in rows:
        try:
            dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
        except:
            continue

        if dt.replace(tzinfo=APP_TZ) < now_local():
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

# ===== РЈР”РђР›Р•РќРР• =====
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

# ===== РќРђРџРћРњРРќРђРќРРЇ =====
async def reminder_loop():
    while True:
        now = now_local()

        cursor.execute("SELECT * FROM trainings")
        trainings_list = cursor.fetchall()

        for t_id, name, date, time, _ in trainings_list:
            try:
                training_dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M").replace(tzinfo=APP_TZ)
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

# ===== РђР’РўРћРћР§РРЎРўРљРђ =====
async def cleanup_loop():
    while True:
        print(f"Проверка очистки: {now_local()}")
        cleanup_trainings()
        await asyncio.sleep(300)  # каждые 5 минут

# ===== ОСНОВА =====
@dp.message(StateFilter(None))
async def handle(message: types.Message, state: FSMContext):

    cleanup_trainings()

    if message.text == "/start":
        await message.answer(
            """Здравствуйте! 👋
Рады приветствовать Вас в женском фитнес-пространстве 💫

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
Выбирай действие ниже 👇""",
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

    elif message.text == "📏 Добавить замеры":
        await message.answer(
            "Введите дату замеров (дд.мм), без года:",
            reply_markup=get_measurement_cancel_kb()
        )
        await state.set_state(AddMeasurement.date)

    # ===== РћРўР§Р•Рў Р’Р•РЎРђ Р”Р›РЇ РђР”РњРРќРђ =====
    elif message.text == "📈 Отчет веса" and message.from_user.id == ADMIN_ID:
        cursor.execute("SELECT DISTINCT user_id, username FROM measurements")
        users = cursor.fetchall()

        if not users:
            await message.answer("Нет данных о замерах")
            return

        text = ""
        for user_id, username in users:
            cursor.execute(
                "SELECT date, weight FROM measurements WHERE user_id=? ORDER BY rowid",
                (user_id,)
            )
            records = cursor.fetchall()
            if not records:
                continue

            text += f"\n<a href='tg://user?id={user_id}'>{username}</a>:\n"
            for date, weight in records:
                text += f"  {date} — {weight} кг\n"

        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=get_clear_list_kb("clear_measurements")
        )

    # ===== РЎРџРРЎРћРљ РЈР§РђРЎРўРќРРљРћР’ =====
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

    elif message.text == "🔁 Управление записями":
        if message.from_user.id != ADMIN_ID:
            return

        kb = get_manage_bookings_kb()
        if kb.inline_keyboard[0][0].text == "Нет записей":
            await message.answer("Сейчас нет активных записей.")
            return

        await message.answer(
            "Выбери тренировку, где нужно отменить или перенести запись:",
            reply_markup=kb
        )

    # ===== РџРћРЎР•Р©Р•РќРРЇ =====
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

        await message.answer(text, reply_markup=get_clear_list_kb("clear_history"))

    # ===== Р”РћР‘РђР’РРўР¬ РўР Р•РќРР РћР’РљРЈ =====
    elif message.text == "➕ Добавить тренировку":
        if message.from_user.id != ADMIN_ID:
            return

        await message.answer("Введите название:")
        await state.set_state(AddTraining.name)

    # ===== РЈР”РђР›РРўР¬ =====
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

        today = now_local().date()
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

# ===== FSM ХЕНДЛЕРЫ ДЛЯ ЗАМЕРОВ =====
@dp.message(AddMeasurement.date)
async def add_measurement_date(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("Меню 👇", reply_markup=get_main_kb(message.from_user.id))
        return

    user_input = message.text.strip()
    try:
        datetime.strptime(f"{user_input}.2000", "%d.%m.%Y")
        await state.update_data(date=user_input)
        await message.answer(
            "Введите вес (кг), можно через точку или запятую:",
            reply_markup=get_measurement_cancel_kb()
        )
        await state.set_state(AddMeasurement.weight)
    except:
        await message.answer("❌ Неверный формат даты! Введите дд.мм, например 25.03")
        return

@dp.message(AddMeasurement.weight)
async def add_measurement_weight(message: types.Message, state: FSMContext):
    if message.text == "⬅️ Назад":
        await state.clear()
        await message.answer("Меню 👇", reply_markup=get_main_kb(message.from_user.id))
        return

    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.full_name

    weight_text = message.text.strip().replace(",", ".")
    try:
        weight = float(weight_text)
    except:
        await message.answer("❌ Неверный формат веса! Введите число через точку или запятую.")
        return

    cursor.execute(
        "INSERT INTO measurements VALUES (?, ?, ?, ?)",
        (user_id, username, data["date"], weight)
    )
    conn.commit()
    append_measurement_row(username, data["date"], weight)

    await message.answer(f"📏 Замер добавлен: {data['date']} — {weight} кг", reply_markup=get_main_kb(message.from_user.id))
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith(("book_", "cancel_", "delete_", "wait_", "clear_", "admin_")))
async def callbacks(callback: types.CallbackQuery):
    cleanup_trainings()
    await callback.answer()

    user_id = callback.from_user.id
    data = callback.data

    # ===== Р—РђРџРРЎР¬ РќРђ РўР Р•РќРР РћР’РљРЈ =====
    if data.startswith("book_"):
        t_id = int(data.split("_")[1])

        cursor.execute("SELECT * FROM trainings WHERE id=?", (t_id,))
        t = cursor.fetchone()
        if not t:
            await callback.message.answer("Не найдена ❌")
            return

        _, name, date, time, slots = t

        # Проверяем, не записан ли уже пользователь
        cursor.execute(
            "SELECT * FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        if cursor.fetchone():
            await callback.message.answer("Ты уже записан")
            return

        # Узнаём текущее количество записей
        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (t_id,))
        count = cursor.fetchone()[0]

        username = callback.from_user.username
        if username:
            username = "@" + username
        else:
            username = callback.from_user.full_name

        if count >= slots:
            # Если мест нет, проверяем очередь
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

        # Записываем пользователя
        cursor.execute(
            "INSERT INTO bookings VALUES (?, ?, ?)",
            (user_id, username, t_id)
        )
        conn.commit()

        await callback.message.answer("✅ Ты записана на тренировку! 💪")
        await callback.message.answer(
            """Информация по оплате тренировок 

💬 По вопросам оплаты, пожалуйста, обращайтесь в личные сообщения: @krotanny

👥 Групповые тренировки:
• Стоимость: 60 рублей в месяц.
• Разовое посещение: 15 рублей.
• Срок оплаты: не позднее последнего рабочего дня текущего месяца. 🗓️

👤 Персональные тренировки:
• Стоимость определяется в индивидуальном порядке в соответствии с заключёнными договорами. 📝

Будьте на спорте и до встречи на тренировке! 💪🏻"""
        )

        # 🔔 уведомление админу, если после этой записи тренировка стала полной
        if count + 1 >= slots:
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"⚠️ Все места заполнены!\n\n{name} {date} {time}"
                )
            except Exception as e:
                print("Ошибка отправки админу:", e)

    # ===== ОТМЕНА =====
    elif data.startswith("cancel_"):
        t_id = int(data.split("_")[1])

        cursor.execute(
            "DELETE FROM bookings WHERE user_id=? AND training_id=?",
            (user_id, t_id)
        )
        conn.commit()

        await callback.message.answer("Запись отменена ❌")

        await notify_waitlist(t_id)

    # ===== РЈР”РђР›Р•РќРР• =====
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

    # ===== ОЧЕРЕДЬ =====
    elif data.startswith("wait_"):
        t_id = int(data.split("_")[1])

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

    elif data == "admin_manage_back":
        if user_id != ADMIN_ID:
            return

        await callback.message.answer(
            "Выбери тренировку, где нужно отменить или перенести запись:",
            reply_markup=get_manage_bookings_kb()
        )

    elif data.startswith("admin_manage_training_"):
        if user_id != ADMIN_ID:
            return

        training_id = int(data.split("_")[-1])
        cursor.execute("SELECT name, date, time FROM trainings WHERE id=?", (training_id,))
        training = cursor.fetchone()
        if not training:
            await callback.message.answer("Тренировка не найдена.")
            return

        name, date, time = training
        await callback.message.answer(
            f"Участники тренировки {get_training_display(name, date, time)}:",
            reply_markup=get_training_users_kb(training_id)
        )

    elif data.startswith("admin_manage_user_"):
        if user_id != ADMIN_ID:
            return

        _, _, _, training_id, booked_user_id = data.split("_")
        training_id = int(training_id)
        booked_user_id = int(booked_user_id)

        cursor.execute(
            """SELECT b.username, t.name, t.date, t.time
               FROM bookings b
               JOIN trainings t ON t.id = b.training_id
               WHERE b.training_id=? AND b.user_id=?""",
            (training_id, booked_user_id)
        )
        row = cursor.fetchone()
        if not row:
            await callback.message.answer("Запись не найдена.")
            return

        username, name, date, time = row
        await callback.message.answer(
            f"Что сделать с {username}?\n\nТекущая тренировка: {get_training_display(name, date, time)}",
            reply_markup=get_manage_user_action_kb(training_id, booked_user_id)
        )

    elif data.startswith("admin_cancel_booking_"):
        if user_id != ADMIN_ID:
            return

        _, _, _, training_id, booked_user_id = data.split("_")
        training_id = int(training_id)
        booked_user_id = int(booked_user_id)

        cursor.execute(
            """SELECT b.username, t.name, t.date, t.time
               FROM bookings b
               JOIN trainings t ON t.id = b.training_id
               WHERE b.training_id=? AND b.user_id=?""",
            (training_id, booked_user_id)
        )
        row = cursor.fetchone()
        if not row:
            await callback.message.answer("Запись уже отсутствует.")
            return

        username, name, date, time = row
        cursor.execute(
            "DELETE FROM bookings WHERE user_id=? AND training_id=?",
            (booked_user_id, training_id)
        )
        conn.commit()

        try:
            await bot.send_message(
                booked_user_id,
                f"❌ Администратор отменил твою запись на тренировку\n\n{get_training_display(name, date, time)}"
            )
        except:
            pass

        await notify_waitlist(training_id)
        await callback.message.answer(f"Запись {username} отменена.")

    elif data.startswith("admin_move_booking_"):
        if user_id != ADMIN_ID:
            return

        _, _, _, training_id, booked_user_id = data.split("_")
        training_id = int(training_id)
        booked_user_id = int(booked_user_id)

        cursor.execute(
            """SELECT b.username, t.name, t.date, t.time
               FROM bookings b
               JOIN trainings t ON t.id = b.training_id
               WHERE b.training_id=? AND b.user_id=?""",
            (training_id, booked_user_id)
        )
        row = cursor.fetchone()
        if not row:
            await callback.message.answer("Запись не найдена.")
            return

        username, name, date, time = row
        await callback.message.answer(
            f"Куда перенести {username}?\n\nСейчас: {get_training_display(name, date, time)}",
            reply_markup=get_move_target_kb(training_id, booked_user_id)
        )

    elif data.startswith("admin_move_to_"):
        if user_id != ADMIN_ID:
            return

        _, _, _, from_training_id, booked_user_id, to_training_id = data.split("_")
        from_training_id = int(from_training_id)
        booked_user_id = int(booked_user_id)
        to_training_id = int(to_training_id)

        cursor.execute(
            """SELECT b.username, t.name, t.date, t.time
               FROM bookings b
               JOIN trainings t ON t.id = b.training_id
               WHERE b.training_id=? AND b.user_id=?""",
            (from_training_id, booked_user_id)
        )
        from_row = cursor.fetchone()
        if not from_row:
            await callback.message.answer("Исходная запись не найдена.")
            return

        username, from_name, from_date, from_time = from_row

        cursor.execute("SELECT name, date, time, slots FROM trainings WHERE id=?", (to_training_id,))
        to_row = cursor.fetchone()
        if not to_row:
            await callback.message.answer("Целевая тренировка не найдена.")
            return

        to_name, to_date, to_time, to_slots = to_row
        cursor.execute("SELECT COUNT(*) FROM bookings WHERE training_id=?", (to_training_id,))
        to_count = cursor.fetchone()[0]
        if to_count >= to_slots:
            await callback.message.answer("На выбранную тренировку уже нет мест.")
            return

        cursor.execute(
            "SELECT 1 FROM bookings WHERE user_id=? AND training_id=?",
            (booked_user_id, to_training_id)
        )
        if cursor.fetchone():
            await callback.message.answer("Этот участник уже записан на выбранную тренировку.")
            return

        cursor.execute(
            "UPDATE bookings SET training_id=? WHERE user_id=? AND training_id=?",
            (to_training_id, booked_user_id, from_training_id)
        )
        cursor.execute(
            "DELETE FROM waitlist WHERE user_id=? AND training_id=?",
            (booked_user_id, to_training_id)
        )
        conn.commit()

        try:
            await bot.send_message(
                booked_user_id,
                "🔁 Администратор перенес твою запись на другую тренировку\n\n"
                f"Было: {get_training_display(from_name, from_date, from_time)}\n"
                f"Стало: {get_training_display(to_name, to_date, to_time)}"
            )
        except:
            pass

        await notify_waitlist(from_training_id)
        await callback.message.answer(
            f"{username} перенесен(а).\n\n"
            f"Было: {get_training_display(from_name, from_date, from_time)}\n"
            f"Стало: {get_training_display(to_name, to_date, to_time)}"
        )

    elif data == "clear_history":
        if user_id != ADMIN_ID:
            return

        cursor.execute("DELETE FROM history")
        conn.commit()
        await callback.message.answer("Список посещений очищен 🗑️")

    elif data == "clear_measurements":
        if user_id != ADMIN_ID:
            return

        cursor.execute("DELETE FROM measurements")
        conn.commit()
        await callback.message.answer("Список замеров очищен 🗑️")

async def main():
    print(f"Бот запущен 🚀 | timezone={APP_TZ}")

    if init_google_sheets():
        print("Google Sheets подключен")
    else:
        print("Google Sheets недоступен, бот продолжит работу с локальной базой")

    cleanup_trainings()  # 👈 сразу при старте

    asyncio.create_task(reminder_loop())
    asyncio.create_task(cleanup_loop())  # 👈 ВОТ СЮДА

    await dp.start_polling(bot)

asyncio.run(main())
