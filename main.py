import requests
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime, timedelta
import time
import threading
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename='bot.log', format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_URL = "https://iis.bsuir.by/api/v1"
TOKEN = "your_token"
bot = telebot.TeleBot(TOKEN)

user_data = {}  # Хранит данные пользователей
reminder_threads = {}  # Хранит потоки напоминаний


class User:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.group_name = None
        self.subgroup = None
        self.receive_reminders = True  # По умолчанию напоминания включены
        self.schedule_format = "full"  # По умолчанию полное расписание

    def set_group(self, group_name):
        self.group_name = group_name

    def set_subgroup(self, subgroup):
        self.subgroup = subgroup

    def toggle_reminders(self, status):
        self.receive_reminders = status

    def set_schedule_format(self, format_type):
        self.schedule_format = format_type


def get_current_week():
    try:
        response = requests.get(f"{API_URL}/schedule/current-week", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Ошибка при получении текущей недели: {e}")
        return None


def format_schedule(schedule, current_week, subgroup, format_type="full"):
    try:
        week_days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        today = datetime.today()
        start_of_week = today - timedelta(days=today.weekday())
        week_dates = {week_days[i]: (start_of_week + timedelta(days=i)).strftime("%d.%m") for i in range(7)}

        if format_type == "today":
            current_day = week_days[today.weekday()]
            result = f"📅 *Расписание на {current_day} ({week_dates[current_day]}):*\n\n"
            days_to_show = [current_day]
        else:
            result = f"📅 *Расписание ({week_dates['Понедельник']} - {week_dates['Воскресенье']}):*\n\n"
            days_to_show = week_days

        for day in days_to_show:
            date = week_dates.get(day, "Неизвестно")
            lessons_for_day = []

            if day in schedule:
                for lesson in schedule[day]:
                    week_numbers = lesson.get("weekNumber")
                    lesson_subgroup = lesson.get("numSubgroup")

                    if (week_numbers is None or current_week in week_numbers) and \
                            (lesson_subgroup is None or lesson_subgroup == 0 or lesson_subgroup == subgroup):
                        subject = lesson["subject"]
                        lesson_type = lesson.get("lessonTypeAbbrev", "")
                        time = f"{lesson['startLessonTime']} - {lesson['endLessonTime']}"
                        auditories = ", ".join(lesson.get("auditories", ["Не указано"]))
                        teacher = ", ".join(
                            f"{t['lastName']} {t['firstName']} {t['middleName']}"
                            for t in lesson.get("employees", [])
                        ) or "Преподаватель не указан"

                        lessons_for_day.append(f"🕒 {time} | {subject} ({lesson_type})\n🏫 {auditories}\n👨‍🏫 {teacher}\n")

            result += f"📌 *{day}, {date}:*\n"
            result += "\n".join(lessons_for_day) if lessons_for_day else "🔸 Нет занятий\n"
            result += "\n"

        return result
    except Exception as e:
        logger.error(f"Ошибка при форматировании расписания: {e}")
        return "⚠ Ошибка при обработке расписания"


def get_schedule(group_number):
    try:
        response = requests.get(f"{API_URL}/schedule?studentGroup={group_number}", timeout=10)
        response.raise_for_status()
        return response.json().get("schedules")
    except requests.RequestException as e:
        logger.error(f"Ошибка при получении расписания: {e}")
        return None


def send_reminder(chat_id, subject, start_time, auditories, teacher):
    try:
        message = (f"⏰ Напоминание: Скоро начнется пара!\n"
                   f"📚 Предмет: *{subject}*\n"
                   f"🕒 Время: {start_time}\n"
                   f"🏫 Аудитория: {auditories}\n"
                   f"👨‍🏫 Преподаватель: {teacher}")
        bot.send_message(chat_id, message, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания: {e}")


def get_previous_lesson_end(schedule, today, lesson_start_time, current_week, subgroup):
    try:
        if today not in schedule:
            return None
        lessons = [l for l in schedule[today] if
                   (l.get("weekNumber") is None or current_week in l.get("weekNumber")) and
                   (l.get("numSubgroup") is None or l.get("numSubgroup") == 0 or l.get("numSubgroup") == subgroup)]
        lessons.sort(key=lambda x: x["endLessonTime"])

        for lesson in lessons:
            end_time = datetime.strptime(f"{datetime.now().strftime('%Y-%m-%d')} {lesson['endLessonTime']}",
                                         "%Y-%m-%d %H:%M")
            start_time = datetime.strptime(f"{datetime.now().strftime('%Y-%m-%d')} {lesson_start_time}",
                                           "%Y-%m-%d %H:%M")
            if end_time < start_time:
                return end_time
        return None
    except Exception as e:
        logger.error(f"Ошибка при поиске конца предыдущей пары: {e}")
        return None


def schedule_reminders(chat_id, schedule, current_week, subgroup):
    try:
        while True:
            now = datetime.now()
            current_day = now.strftime("%A")
            day_mapping = {
                "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
                "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота",
                "Sunday": "Воскресенье"
            }
            today = day_mapping.get(current_day)

            if today in schedule:
                for lesson in schedule[today]:
                    week_numbers = lesson.get("weekNumber")
                    lesson_subgroup = lesson.get("numSubgroup")

                    if (week_numbers is None or current_week in week_numbers) and \
                            (lesson_subgroup is None or lesson_subgroup == 0 or lesson_subgroup == subgroup):
                        lesson_time = lesson['startLessonTime']
                        lesson_start = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {lesson_time}", "%Y-%m-%d %H:%M")

                        prev_end = get_previous_lesson_end(schedule, today, lesson_time, current_week, subgroup)
                        if prev_end:
                            reminder_time = prev_end
                        else:
                            reminder_time = lesson_start - timedelta(hours=1)

                        time_diff = (lesson_start - now).total_seconds()
                        reminder_diff = (reminder_time - now).total_seconds()

                        if 0 < reminder_diff <= 60 and time_diff > 0:
                            subject = lesson["subject"]
                            auditories = ", ".join(lesson.get("auditories", ["Не указано"]))
                            teacher = ", ".join(
                                f"{t['lastName']} {t['firstName']} {t['middleName']}"
                                for t in lesson.get("employees", [])
                            ) or "Преподаватель не указан"
                            send_reminder(chat_id, subject, lesson_time, auditories, teacher)
                            time.sleep(61)

            time.sleep(60)
    except Exception as e:
        logger.error(f"Ошибка в потоке напоминаний: {e}")


@bot.message_handler(commands=["start"])
def start(message):
    try:
        chat_id = message.chat.id
        if chat_id not in user_data:
            user_data[chat_id] = User(chat_id)

        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("⚙ Настройки"), KeyboardButton("📅 Расписание"))
        markup.add(KeyboardButton("🔍 Расписание другой группы"))
        bot.send_message(chat_id, "Привет! 👋 Я помогу тебе с расписанием. Выбери действие:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка в команде /start: {e}")
        bot.send_message(chat_id, "⚠ Произошла ошибка, попробуйте позже")


@bot.message_handler(func=lambda message: message.text == "⚙ Настройки")
def settings(message):
    try:
        chat_id = message.chat.id
        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton("📚 Изменить группу"), KeyboardButton("⏰ Напоминания"),
                   KeyboardButton("📋 Формат расписания"), KeyboardButton("↩ Назад"))
        bot.send_message(chat_id, "⚙ Выберите, что настроить:", reply_markup=markup)
        bot.register_next_step_handler(message, process_settings)
    except Exception as e:
        logger.error(f"Ошибка в настройках: {e}")


def process_settings(message):
    try:
        chat_id = message.chat.id
        if message.text == "📚 Изменить группу":
            bot.send_message(chat_id, "📂 Введите номер группы:")
            bot.register_next_step_handler(message, choose_group_manually)
        elif message.text == "⏰ Напоминания":
            markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add(KeyboardButton("Включить"), KeyboardButton("Выключить"))
            bot.send_message(chat_id, "⏰ Выберите статус напоминаний:", reply_markup=markup)
            bot.register_next_step_handler(message, toggle_reminders)
        elif message.text == "📋 Формат расписания":
            markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
            markup.add(KeyboardButton("Полное"), KeyboardButton("Только сегодня"))
            bot.send_message(chat_id, "📋 Выберите формат расписания:", reply_markup=markup)
            bot.register_next_step_handler(message, set_schedule_format)
        elif message.text == "↩ Назад":
            start(message)
    except Exception as e:
        logger.error(f"Ошибка при обработке настроек: {e}")


def choose_group_manually(message):
    try:
        chat_id = message.chat.id
        group_name = message.text.strip().upper()
        user_data[chat_id].set_group(group_name)

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton("1"), KeyboardButton("2"), KeyboardButton("Нет подгруппы"))
        bot.send_message(chat_id, f"✅ Вы выбрали группу: {group_name}\n📂 Теперь выберите подгруппу:",
                         reply_markup=markup)
        bot.register_next_step_handler(message, choose_subgroup)
    except Exception as e:
        logger.error(f"Ошибка при выборе группы: {e}")


def choose_subgroup(message):
    try:
        chat_id = message.chat.id
        text = message.text.strip()

        if text in ["1", "2"]:
            user_data[chat_id].set_subgroup(int(text))
        elif text == "Нет подгруппы":
            user_data[chat_id].set_subgroup(None)
        else:
            bot.send_message(chat_id, "❌ Выберите подгруппу из предложенных вариантов.")
            return

        bot.send_message(chat_id, f"✅ Подгруппа выбрана: {text}")
        start(message)
    except Exception as e:
        logger.error(f"Ошибка при выборе подгруппы: {e}")


def toggle_reminders(message):
    try:
        chat_id = message.chat.id
        if message.text == "Включить":
            user_data[chat_id].toggle_reminders(True)
            bot.send_message(chat_id, "✅ Напоминания включены")
        elif message.text == "Выключить":
            user_data[chat_id].toggle_reminders(False)
            bot.send_message(chat_id, "✅ Напоминания выключены")
        start(message)
    except Exception as e:
        logger.error(f"Ошибка при переключении напоминаний: {e}")


def set_schedule_format(message):
    try:
        chat_id = message.chat.id
        if message.text == "Полное":
            user_data[chat_id].set_schedule_format("full")
            bot.send_message(chat_id, "✅ Выбран полный формат расписания")
        elif message.text == "Только сегодня":
            user_data[chat_id].set_schedule_format("today")
            bot.send_message(chat_id, "✅ Выбран формат только на сегодня")
        start(message)
    except Exception as e:
        logger.error(f"Ошибка при выборе формата расписания: {e}")


@bot.message_handler(commands=["schedule"])
@bot.message_handler(func=lambda message: message.text == "📅 Расписание")
def send_schedule(message):
    try:
        chat_id = message.chat.id
        user = user_data.get(chat_id)

        if not user or not user.group_name:
            bot.send_message(chat_id, "⚠ Сначала настройте группу в ⚙ Настройки")
            return

        schedule = get_schedule(user.group_name)
        if not schedule:
            bot.send_message(chat_id, "📌 Расписание не найдено!")
            return

        current_week = get_current_week()
        if current_week is None:
            bot.send_message(chat_id, "⚠ Ошибка при получении номера недели!")
            return

        bot.send_message(chat_id, format_schedule(schedule, current_week, user.subgroup, user.schedule_format),
                         parse_mode='Markdown')

        if user.receive_reminders:
            if chat_id in reminder_threads:
                del reminder_threads[chat_id]
            reminder_thread = threading.Thread(target=schedule_reminders,
                                               args=(chat_id, schedule, current_week, user.subgroup))
            reminder_thread.daemon = True
            reminder_thread.start()
            reminder_threads[chat_id] = reminder_thread

    except Exception as e:
        logger.error(f"Ошибка при отправке расписания: {e}")
        bot.send_message(chat_id, "⚠ Произошла ошибка при получении расписания")


@bot.message_handler(func=lambda message: message.text == "🔍 Расписание другой группы")
def other_group_schedule(message):
    try:
        chat_id = message.chat.id
        bot.send_message(chat_id, "📂 Введите номер группы, расписание которой хотите посмотреть:")
        bot.register_next_step_handler(message, process_other_group)
    except Exception as e:
        logger.error(f"Ошибка при запросе расписания другой группы: {e}")


def process_other_group(message):
    try:
        chat_id = message.chat.id
        group_name = message.text.strip().upper()

        markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(KeyboardButton("1"), KeyboardButton("2"), KeyboardButton("Нет подгруппы"))
        bot.send_message(chat_id, f"🔍 Вы выбрали группу: {group_name}\n📂 Выберите подгруппу:", reply_markup=markup)
        bot.register_next_step_handler(message, process_other_group_subgroup, group_name)
    except Exception as e:
        logger.error(f"Ошибка при обработке группы: {e}")


def process_other_group_subgroup(message, group_name):
    try:
        chat_id = message.chat.id
        text = message.text.strip()
        subgroup = None

        if text in ["1", "2"]:
            subgroup = int(text)
        elif text != "Нет подгруппы":
            bot.send_message(chat_id, "❌ Выберите подгруппу из предложенных вариантов.")
            return

        schedule = get_schedule(group_name)
        if not schedule:
            bot.send_message(chat_id, "📌 Расписание не найдено!")
            return

        current_week = get_current_week()
        if current_week is None:
            bot.send_message(chat_id, "⚠ Ошибка при получении номера недели!")
            return

        user = user_data.get(chat_id)
        format_type = user.schedule_format if user else "full"  # Используем формат пользователя, если он есть
        bot.send_message(chat_id, format_schedule(schedule, current_week, subgroup, format_type), parse_mode='Markdown')
        start(message)  # Возвращаемся в главное меню
    except Exception as e:
        logger.error(f"Ошибка при обработке подгруппы другой группы: {e}")
        bot.send_message(chat_id, "⚠ Произошла ошибка")


if __name__ == "__main__":
    try:
        logger.info("Бот запущен")
        bot.polling(none_stop=True)
    except Exception as e:
        logger.error(f"Ошибка в основном цикле бота: {e}")
bot.infinity_polling()