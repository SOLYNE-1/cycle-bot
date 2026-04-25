# Cycle Bot

Telegram-бот для отслеживания менструального цикла с персональными советами от Claude AI.

## Возможности

| Команда | Описание |
|---|---|
| `/start` | Приветствие и выбор длины цикла |
| `/period` | Отметить начало нового цикла |
| `/status` | Текущий день, фаза, дата следующей менструации + совет от AI |
| `/settings` | Изменить длину цикла |

**Напоминание** автоматически отправляется за 2 дня до ожидаемого начала менструации.

### Фазы цикла

| Фаза | Дни |
|---|---|
| 🩸 Менструация | 1–5 |
| 🌱 Фолликулярная | 6–13 |
| 🌸 Овуляция | 14–16 |
| 🌙 Лютеиновая | 17–конец цикла |

## Установка

### 1. Клонировать репозиторий

```bash
git clone <repo-url>
cd cycle-bot
```

### 2. Создать виртуальное окружение

```bash
python -m venv venv
source venv/bin/activate   # macOS / Linux
# или
venv\Scripts\activate      # Windows
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

### 4. Настроить переменные окружения

```bash
cp .env.example .env
```

Открой `.env` и заполни:

```env
TELEGRAM_TOKEN=токен_от_BotFather
ANTHROPIC_API_KEY=ключ_от_console.anthropic.com
```

**Как получить токены:**
- Telegram: напиши [@BotFather](https://t.me/BotFather) → `/newbot`
- Anthropic: [console.anthropic.com](https://console.anthropic.com) → API Keys

### 5. Запустить бота

```bash
python bot.py
```

## Структура данных

Данные хранятся в `cycle_data.json`. Формат:

```json
{
  "123456789": {
    "cycle_length": 28,
    "last_period_start": "2025-04-01",
    "reminder_sent": false
  }
}
```

Каждый пользователь хранится по своему `chat_id`. Файл создаётся автоматически при первом использовании.

## Технологии

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram Bot API
- [Anthropic SDK](https://github.com/anthropics/anthropic-sdk-python) — советы от Claude Haiku
- [APScheduler](https://apscheduler.readthedocs.io) — ежедневные напоминания в 09:00
- [python-dotenv](https://github.com/theskumar/python-dotenv) — переменные окружения
