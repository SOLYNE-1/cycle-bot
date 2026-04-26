import os
import json
import logging
import calendar as cal_module
from datetime import datetime, date, timedelta
from typing import Optional

from dotenv import load_dotenv
import anthropic
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DATA_FILE = "cycle_data.json"

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

PHASES = {
    "menstruation": ("🩸 Менструация", range(1, 6)),
    "follicular": ("🌱 Фолликулярная фаза", range(6, 14)),
    "ovulation": ("🌸 Овуляция", range(14, 17)),
    "luteal": ("🌙 Лютеиновая фаза", None),
}

PHASE_DESCRIPTIONS = {
    "menstruation": "менструации (дни 1–5)",
    "follicular": "фолликулярной фазы (дни 6–13): уровень эстрогена растёт, энергия повышается",
    "ovulation": "овуляции (дни 14–16): пик фертильности и энергии",
    "luteal": "лютеиновой фазы (дни 17+): прогестерон растёт, возможен ПМС ближе к концу",
}

CYCLE_LENGTHS = [26, 28, 30, 32]

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

MONTH_GENITIVE = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}

MENU_NEWCYCLE = "🩸 Начать цикл"
MENU_STATUS   = "📊 Мой статус"
MENU_NEXT     = "⏰ Следующий цикл"
MENU_HISTORY  = "📋 История"
MENU_SETTINGS = "⚙️ Настройки"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_cycle_info(user_data: dict) -> tuple[int, str, str, date]:
    cycle_length: int = user_data["cycle_length"]
    last_period = datetime.strptime(user_data["last_period_start"], "%Y-%m-%d").date()
    today = date.today()

    days_since = (today - last_period).days
    current_day = (days_since % cycle_length) + 1

    completed_cycles = days_since // cycle_length
    next_period = last_period + timedelta(days=cycle_length * (completed_cycles + 1))

    if current_day <= 5:
        phase_key = "menstruation"
    elif current_day <= 13:
        phase_key = "follicular"
    elif current_day <= 16:
        phase_key = "ovulation"
    else:
        phase_key = "luteal"

    phase_label = PHASES[phase_key][0]
    return current_day, phase_label, phase_key, next_period


# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------

def get_claude_advice(phase_key: str, day: int) -> str:
    description = PHASE_DESCRIPTIONS[phase_key]
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": (
                        "Ты заботливый помощник по женскому здоровью. "
                        "Давай короткие, практичные и тёплые советы на русском языке. "
                        "Отвечай 2–3 предложениями, без вступлений и заголовков."
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Сегодня {day}-й день цикла, фаза {description}. "
                        "Дай один конкретный совет по питанию или самочувствию для этой фазы."
                    ),
                }
            ],
        )
        return response.content[0].text
    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return "Пейте достаточно воды, двигайтесь в своём ритме и прислушивайтесь к своему телу 💙"


# ---------------------------------------------------------------------------
# Keyboard helpers
# ---------------------------------------------------------------------------

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(MENU_NEWCYCLE), KeyboardButton(MENU_STATUS)],
            [KeyboardButton(MENU_NEXT),     KeyboardButton(MENU_HISTORY)],
            [KeyboardButton(MENU_SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def ru_date(d: date) -> str:
    return f"{d.day} {MONTH_GENITIVE[d.month]}"


def cycle_when_keyboard() -> InlineKeyboardMarkup:
    today = date.today()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ Сегодня ({ru_date(today)})", callback_data="cycle_today"),
        InlineKeyboardButton("📅 Другая дата", callback_data="cycle_other_date"),
    ]])


def cycle_date_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data="cycle_date_yes"),
        InlineKeyboardButton("✏️ Ввести снова", callback_data="cycle_date_retry"),
    ]])


def cycle_length_keyboard() -> InlineKeyboardMarkup:
    row1 = [InlineKeyboardButton(f"{n} дн.", callback_data=f"cycle_{n}") for n in CYCLE_LENGTHS[:2]]
    row2 = [InlineKeyboardButton(f"{n} дн.", callback_data=f"cycle_{n}") for n in CYCLE_LENGTHS[2:]]
    return InlineKeyboardMarkup([row1, row2])


def build_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    today = date.today()
    # allow going back up to 3 months
    min_date = today.replace(day=1) - timedelta(days=62)

    rows = []

    # Navigation row
    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)

    can_prev = date(prev_y, prev_m, 1) >= min_date
    can_next = date(next_y, next_m, 1) <= date(today.year, today.month, 1)

    nav = [
        InlineKeyboardButton("◀", callback_data=f"cal_nav_{prev_y}_{prev_m}") if can_prev
        else InlineKeyboardButton(" ", callback_data="cal_ignore"),
        InlineKeyboardButton(f"{MONTH_NAMES[month]} {year}", callback_data="cal_ignore"),
        InlineKeyboardButton("▶", callback_data=f"cal_nav_{next_y}_{next_m}") if can_next
        else InlineKeyboardButton(" ", callback_data="cal_ignore"),
    ]
    rows.append(nav)

    # Weekday headers (Mon–Sun)
    rows.append([
        InlineKeyboardButton(d, callback_data="cal_ignore")
        for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    ])

    # Day cells
    for week in cal_module.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="cal_ignore"))
            else:
                d = date(year, month, day)
                if d > today or d < min_date:
                    row.append(InlineKeyboardButton("·", callback_data="cal_ignore"))
                else:
                    label = f"[{day}]" if d == today else str(day)
                    row.append(InlineKeyboardButton(label, callback_data=f"cal_day_{year}_{month}_{day}"))
        rows.append(row)

    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я помогу отслеживать твой менструальный цикл.\n\n"
        "Кнопки меню всегда доступны внизу 👇",
        reply_markup=main_menu(),
    )
    await update.message.reply_text(
        "Шаг 1 из 2 — какова средняя длина твоего цикла?",
        reply_markup=cycle_length_keyboard(),
    )


async def callback_set_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    cycle_length = int(query.data.split("_")[1])
    chat_id = str(query.message.chat_id)

    data = load_data()
    if chat_id not in data:
        data[chat_id] = {}
    data[chat_id]["cycle_length"] = cycle_length
    data[chat_id].setdefault("reminder_sent", False)
    save_data(data)

    today = date.today()
    await query.edit_message_text(
        f"✅ Длина цикла: *{cycle_length} дней*\n\n"
        "Шаг 2 из 2 — выбери дату начала *последней* менструации:\n"
        "_(сегодня выделено в [скобках])_",
        parse_mode="Markdown",
        reply_markup=build_calendar(today.year, today.month),
    )


async def callback_cal_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def callback_cal_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # cal_nav_YYYY_MM
    _, _, y, m = query.data.split("_")
    await query.edit_message_reply_markup(reply_markup=build_calendar(int(y), int(m)))


async def callback_cal_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    # cal_day_YYYY_MM_DD
    _, _, y, m, d = query.data.split("_")
    selected = date(int(y), int(m), int(d))
    chat_id = str(query.message.chat_id)

    data = load_data()
    if chat_id not in data:
        data[chat_id] = {}
    data[chat_id]["last_period_start"] = selected.strftime("%Y-%m-%d")
    data[chat_id]["reminder_sent"] = False
    save_data(data)

    cycle_length = data[chat_id].get("cycle_length", 28)
    next_period = selected + timedelta(days=cycle_length)

    await query.edit_message_text(
        f"✅ Дата начала цикла: *{selected.strftime('%d.%m.%Y')}*\n\n"
        f"📅 Следующая менструация ожидается: *{next_period.strftime('%d.%m.%Y')}*\n\n"
        f"Всё готово! Пользуйся кнопками меню внизу 👇",
        parse_mode="Markdown",
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Меню готово:",
        reply_markup=main_menu(),
    )


# ---------------------------------------------------------------------------
# Cycle start flow
# ---------------------------------------------------------------------------

def cycle_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, началось", callback_data="cycle_start_yes"),
        InlineKeyboardButton("➡️ Ещё нет", callback_data="cycle_start_no"),
    ]])


def cycle_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, записать", callback_data="cycle_start_confirm"),
        InlineKeyboardButton("❌ Отмена", callback_data="cycle_start_cancel"),
    ]])


def record_new_cycle(chat_id: str, data: dict, cycle_date: Optional[date] = None) -> str:
    target = cycle_date or date.today()
    user_data = data[chat_id]
    cycle_length: int = user_data.get("cycle_length", 28)

    actual_days = None
    if "last_period_start" in user_data:
        last_period = datetime.strptime(user_data["last_period_start"], "%Y-%m-%d").date()
        actual_days = (target - last_period).days
        history = user_data.setdefault("cycle_history", [])
        if user_data["last_period_start"] not in history:
            history.append(user_data["last_period_start"])

    user_data["last_period_start"] = target.isoformat()
    user_data["reminder_sent"] = False
    user_data["cycle_prompt_pending"] = False
    user_data["cycle_start_prompt_date"] = date.today().isoformat()
    save_data(data)

    next_period = target + timedelta(days=cycle_length)
    msg = f"Записала! 🌸\n\n📅 Следующий цикл ожидается: *{next_period.strftime('%d.%m.%Y')}*"

    if actual_days is not None and actual_days > 0:
        diff = actual_days - cycle_length
        if diff == 0:
            msg += f"\n\nТвой цикл оказался ровно *{actual_days} дней* — точно по графику!"
        elif diff < 0:
            msg += f"\n\nТвой цикл оказался *{actual_days} дней* — это короче обычного на *{abs(diff)} дн.*"
        else:
            msg += f"\n\nТвой цикл оказался *{actual_days} дней* — это длиннее обычного на *{diff} дн.*"

    return msg


async def cmd_newcycle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()

    if chat_id not in data or "cycle_length" not in data[chat_id]:
        await update.message.reply_text("⚠️ Сначала настрой бота через /start", reply_markup=main_menu())
        return

    await update.message.reply_text(
        "Когда начался цикл? 🩸",
        reply_markup=cycle_when_keyboard(),
    )


async def callback_cycle_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = load_data()

    if chat_id not in data:
        await query.edit_message_text("⚠️ Данные не найдены. Запусти /start")
        return

    msg = record_new_cycle(chat_id, data)
    await query.edit_message_text(msg, parse_mode="Markdown")


async def callback_cycle_other_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_cycle_date"] = True
    await query.edit_message_text(
        "Введи дату в формате ДД.ММ\n_Например: 18.04_",
        parse_mode="Markdown",
    )


async def handle_cycle_date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    today = date.today()

    try:
        parts = text.split(".")
        if len(parts) < 2:
            raise ValueError
        day, month = int(parts[0]), int(parts[1])
        parsed = date(today.year, month, day)
        if parsed > today:
            parsed = date(today.year - 1, month, day)
    except (ValueError, IndexError):
        await update.message.reply_text(
            "⚠️ Не могу распознать дату. Введи в формате ДД.ММ, например: 18.04"
        )
        return

    if parsed > today:
        await update.message.reply_text(
            "⚠️ Дата не может быть в будущем. Введи снова:"
        )
        return

    context.user_data["awaiting_cycle_date"] = False
    context.user_data["pending_cycle_date"] = parsed.isoformat()

    days_ago = (today - parsed).days
    if days_ago > 60:
        await update.message.reply_text(
            f"⚠️ *{ru_date(parsed)}* — это больше 2 месяцев назад, всё верно?",
            parse_mode="Markdown",
            reply_markup=cycle_date_confirm_keyboard(),
        )
    else:
        await update.message.reply_text(
            f"Записать *{ru_date(parsed)}*? Это правильно?",
            parse_mode="Markdown",
            reply_markup=cycle_date_confirm_keyboard(),
        )


async def callback_cycle_date_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = load_data()

    if chat_id not in data:
        await query.edit_message_text("⚠️ Данные не найдены. Запусти /start")
        return

    pending = context.user_data.pop("pending_cycle_date", None)
    cycle_date = date.fromisoformat(pending) if pending else date.today()
    msg = record_new_cycle(chat_id, data, cycle_date)
    await query.edit_message_text(msg, parse_mode="Markdown")


async def callback_cycle_date_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_cycle_date"] = True
    context.user_data.pop("pending_cycle_date", None)
    await query.edit_message_text(
        "Введи дату в формате ДД.ММ\n_Например: 18.04_",
        parse_mode="Markdown",
    )


async def callback_cycle_start_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ 'Да, началось' на утреннее напоминание — всегда записывает сегодня."""
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = load_data()

    if chat_id not in data:
        await query.edit_message_text("⚠️ Данные не найдены. Запусти /start")
        return

    msg = record_new_cycle(chat_id, data)
    await query.edit_message_text(msg, parse_mode="Markdown")


async def callback_cycle_start_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ 'Ещё нет' на утреннее напоминание."""
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    data = load_data()

    if chat_id in data:
        data[chat_id]["cycle_prompt_pending"] = True
        data[chat_id]["cycle_start_prompt_date"] = date.today().isoformat()
        save_data(data)

    await query.edit_message_text("Хорошо, спрошу снова завтра 💙")


async def cmd_period(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()

    if chat_id not in data or "cycle_length" not in data[chat_id]:
        await update.message.reply_text("⚠️ Сначала выбери длину цикла через /start")
        return

    today = date.today()
    await update.message.reply_text(
        "📅 Выбери дату начала менструации:\n"
        "_(сегодня выделено в [скобках])_",
        parse_mode="Markdown",
        reply_markup=build_calendar(today.year, today.month),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()

    if chat_id not in data or "cycle_length" not in data[chat_id]:
        await update.message.reply_text("⚠️ Сначала выбери длину цикла через /start")
        return

    if "last_period_start" not in data[chat_id]:
        await update.message.reply_text("⚠️ Отметь начало цикла командой /period")
        return

    user_data = data[chat_id]
    current_day, phase_label, phase_key, next_period = get_cycle_info(user_data)
    days_until = (next_period - date.today()).days

    thinking_msg = await update.message.reply_text("⏳ Получаю совет от Claude...")
    advice = get_claude_advice(phase_key, current_day)
    await thinking_msg.delete()

    await update.message.reply_text(
        f"📊 *Твой цикл сегодня*\n\n"
        f"🗓 День: *{current_day}* из {user_data['cycle_length']}\n"
        f"🔄 Фаза: {phase_label}\n"
        f"📅 Следующая менструация: *{next_period.strftime('%d.%m.%Y')}* (через {days_until} дн.)\n\n"
        f"💡 *Совет:*\n{advice}",
        parse_mode="Markdown",
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()
    current = data.get(chat_id, {}).get("cycle_length", "не задана")

    await update.message.reply_text(
        f"⚙️ *Настройки*\n\n"
        f"Текущая длина цикла: *{current} дней*\n\n"
        f"Выбери новую длину:",
        reply_markup=cycle_length_keyboard(),
        parse_mode="Markdown",
    )


async def cmd_next_cycle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()

    if chat_id not in data or "cycle_length" not in data[chat_id]:
        await update.message.reply_text(
            "⚠️ Сначала настрой бота — нажми /start",
            reply_markup=main_menu(),
        )
        return

    if "last_period_start" not in data[chat_id]:
        await update.message.reply_text(
            "⚠️ Отметь начало цикла — нажми 🩸 Начать цикл",
            reply_markup=main_menu(),
        )
        return

    _, _, _, next_period = get_cycle_info(data[chat_id])
    days_until = (next_period - date.today()).days

    if days_until == 0:
        text = "🩸 Следующая менструация ожидается *сегодня*!"
    elif days_until < 0:
        text = (
            f"🩸 Менструация должна была начаться *{abs(days_until)} дн. назад*\n"
            "Не забудь отметить начало нового цикла 👇"
        )
    else:
        text = (
            f"📅 Следующий цикл: *{next_period.strftime('%d.%m.%Y')}*\n"
            f"Осталось: *{days_until} дн.*"
        )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu())


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    menu_texts = {MENU_NEWCYCLE, MENU_STATUS, MENU_NEXT, MENU_HISTORY, MENU_SETTINGS}

    if context.user_data.get("awaiting_cycle_date") and text not in menu_texts:
        await handle_cycle_date_input(update, context)
        return

    context.user_data.pop("awaiting_cycle_date", None)

    if text == MENU_NEWCYCLE:
        await cmd_newcycle(update, context)
    elif text == MENU_STATUS:
        await cmd_status(update, context)
    elif text == MENU_NEXT:
        await cmd_next_cycle(update, context)
    elif text == MENU_HISTORY:
        await cmd_history(update, context)
    elif text == MENU_SETTINGS:
        await cmd_settings(update, context)


async def cmd_history(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = str(update.message.chat_id)
    data = load_data()

    if chat_id not in data or "last_period_start" not in data[chat_id]:
        await update.message.reply_text(
            "⚠️ Нет данных о циклах. Нажми 🩸 Начать цикл",
            reply_markup=main_menu(),
        )
        return

    user_data = data[chat_id]
    today = date.today()

    history_strs = user_data.get("cycle_history", [])
    current_start_str = user_data["last_period_start"]

    all_starts = sorted(
        {datetime.strptime(d, "%Y-%m-%d").date() for d in history_strs + [current_start_str]}
    )

    lines = ["🗓 *История твоих циклов*\n"]
    completed_lengths = []

    for i, start in enumerate(all_starts):
        num = i + 1
        if i < len(all_starts) - 1:
            end = all_starts[i + 1]
            length = (end - start).days
            completed_lengths.append(length)
            lines.append(f"{num}\\. Цикл: {start.strftime('%d.%m')} — {end.strftime('%d.%m')} ({length} дней) ✅")
        else:
            days_running = (today - start).days + 1
            lines.append(f"{num}\\. Текущий цикл: начался {start.strftime('%d.%m')} (идёт {days_running} дн\\.)")

    lines.append("")

    if len(completed_lengths) < 2:
        lines.append(
            "Пока недостаточно данных для статистики\\.\n"
            "Запиши хотя бы 2 цикла\\!"
        )
    else:
        avg = sum(completed_lengths) / len(completed_lengths)
        shortest = min(completed_lengths)
        longest = max(completed_lengths)
        total = len(all_starts)
        lines.append("📊 *Твоя статистика:*")
        lines.append(f"\\- Средняя длина цикла: {avg:.1f} дней")
        lines.append(f"\\- Самый короткий: {shortest} дней")
        lines.append(f"\\- Самый длинный: {longest} дней")
        lines.append(f"\\- Всего циклов записано: {total}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=main_menu(),
    )


# ---------------------------------------------------------------------------
# Reminder job
# ---------------------------------------------------------------------------

async def send_reminders(application: Application) -> None:
    data = load_data()
    today = date.today()
    changed = False

    for chat_id, user_data in data.items():
        if "last_period_start" not in user_data:
            continue

        _, _, _, next_period = get_cycle_info(user_data)
        days_until = (next_period - today).days

        if days_until > 2 and user_data.get("reminder_sent"):
            data[chat_id]["reminder_sent"] = False
            changed = True

        if days_until == 2 and not user_data.get("reminder_sent", False):
            try:
                await application.bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        "🔔 *Напоминание*\n\n"
                        f"Через 2 дня ожидается начало менструации — *{next_period.strftime('%d.%m.%Y')}*.\n"
                        "Подготовь всё необходимое заранее 💙\n\n"
                        "Используй /status для актуальной информации."
                    ),
                    parse_mode="Markdown",
                )
                data[chat_id]["reminder_sent"] = True
                changed = True
                logger.info("Reminder sent to %s", chat_id)
            except Exception as exc:
                logger.error("Failed to send reminder to %s: %s", chat_id, exc)

    if changed:
        save_data(data)


# ---------------------------------------------------------------------------
# Cycle start prompt job (10:00)
# ---------------------------------------------------------------------------

async def send_cycle_start_prompts(application: Application) -> None:
    data = load_data()
    today = date.today()

    for chat_id, user_data in data.items():
        if "last_period_start" not in user_data:
            continue

        _, _, _, next_period = get_cycle_info(user_data)
        days_until = (next_period - today).days

        last_prompt = user_data.get("cycle_start_prompt_date")
        prompt_pending = user_data.get("cycle_prompt_pending", False)

        # Send if today is the expected start day (and not already prompted today),
        # or if the user said "Ещё нет" on a previous day.
        should_send = False
        if days_until == 0 and last_prompt != today.isoformat():
            should_send = True
        elif prompt_pending and last_prompt and last_prompt != today.isoformat():
            should_send = True

        if should_send:
            try:
                await application.bot.send_message(
                    chat_id=int(chat_id),
                    text="Сегодня ожидается начало цикла 🩸 Началось?",
                    reply_markup=cycle_start_keyboard(),
                )
                data[chat_id]["cycle_start_prompt_date"] = today.isoformat()
                logger.info("Cycle start prompt sent to %s", chat_id)
            except Exception as exc:
                logger.error("Failed to send cycle start prompt to %s: %s", chat_id, exc)

    save_data(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def post_init(application: Application) -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_reminders,
        trigger="cron",
        hour=9,
        minute=0,
        args=[application],
    )
    scheduler.add_job(
        send_cycle_start_prompts,
        trigger="cron",
        hour=10,
        minute=0,
        args=[application],
    )
    scheduler.start()
    logger.info("Scheduler started — reminders at 09:00, cycle prompts at 10:00")


def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN is not set in .env")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set in .env")

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("period", cmd_period))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("settings", cmd_settings))
    application.add_handler(CommandHandler("newcycle", cmd_newcycle))
    application.add_handler(CommandHandler("next", cmd_next_cycle))
    application.add_handler(CallbackQueryHandler(callback_set_cycle, pattern=r"^cycle_\d+$"))
    application.add_handler(CallbackQueryHandler(callback_cal_ignore, pattern=r"^cal_ignore$"))
    application.add_handler(CallbackQueryHandler(callback_cal_nav, pattern=r"^cal_nav_\d+_\d+$"))
    application.add_handler(CallbackQueryHandler(callback_cal_day, pattern=r"^cal_day_\d+_\d+_\d+$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_today,      pattern=r"^cycle_today$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_other_date, pattern=r"^cycle_other_date$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_date_yes,   pattern=r"^cycle_date_yes$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_date_retry, pattern=r"^cycle_date_retry$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_start_yes,  pattern=r"^cycle_start_yes$"))
    application.add_handler(CallbackQueryHandler(callback_cycle_start_no,   pattern=r"^cycle_start_no$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_button))

    logger.info("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
