"""Настройки: читаются из файла .env рядом с ботом или из переменных окружения."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env() -> None:
    p = BASE / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# Впиши токен от @BotFather прямо сюда между кавычками (или оставь пустым и используй .env)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8138099746:AAHHODoDLFn6x4CbML8wSrfMoQpCYPxXdxw")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "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")
OWNER_ID = _int("OWNER_ID", 1973039502)          # твой Telegram ID; бот отвечает только тебе
HOME_CHAT_ID = _int("HOME_CHAT_ID", 0)  # куда слать напоминания (по умолчанию = OWNER_ID)

TZ = os.environ.get("BOT_TZ", "Asia/Omsk")
BASE_CURRENCY = os.environ.get("BASE_CURRENCY", "RUB").upper()  # в ней считаются баланс и статистика
DB_PATH = os.environ.get("DB_PATH", str(BASE / "data" / "life.db"))
LOG_PATH = os.environ.get("LOG_PATH", str(BASE / "data" / "bot.log"))

# Модели Groq по очереди: если у первой кончился лимит, берётся следующая
LLM_MODELS = [m.strip() for m in os.environ.get(
    "LLM_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant").split(",") if m.strip()]
STT_MODEL = os.environ.get("STT_MODEL", "whisper-large-v3-turbo")
GROQ_BASE = os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")
TG_BASE = os.environ.get("TG_BASE", "https://api.telegram.org")
RATES_URL = os.environ.get("RATES_URL", "https://www.cbr-xml-daily.ru/daily_json.js")
RATES_URL_2 = os.environ.get("RATES_URL_2", "https://www.cbr.ru/scripts/XML_daily.asp")

MORNING_TIME = os.environ.get("MORNING_TIME", "09:00")       # утренняя сводка
DEFAULT_REMIND_HOUR = _int("DEFAULT_REMIND_HOUR", 9)          # если у задачи дата без времени
WEEKLY_TIME = os.environ.get("WEEKLY_TIME", "20:00")          # итоги недели (воскресенье)
BACKUP_TIME = os.environ.get("BACKUP_TIME", "21:00")          # бэкап базы (воскресенье)
LEAD_DAYS = (3, 1)                                            # за сколько дней напоминать о платеже
