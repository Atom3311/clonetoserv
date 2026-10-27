import asyncio
import json
import logging
import os
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

# ---------------- КОНФИГУРАЦИЯ ----------------
# Токен берём из переменной окружения:  export BOT_TOKEN="123:ABC..."
BOT_TOKEN = "8153854142:AAHiIdFQgxmAFbTB8u7u29rJqefDGvsua7s"
if not BOT_TOKEN:
    raise SystemExit("Не задана переменная окружения BOT_TOKEN")

COOLDOWN_6_HOURS = 6 * 3600  # 21600 секунд

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

BASE_DIR = Path(__file__).resolve().parent
IMG_DIR = BASE_DIR / "img"
ACTIVITY_FILE = BASE_DIR / "activity.json"
LANG_FILE = BASE_DIR / "lang.json"

# { chat_id: [msg_id1, msg_id2] } — сообщения для удаления
user_messages = {}
# { chat_id: business_connection_id }
business_connections = {}


# ---------------- ТАЙМЕРЫ АКТИВНОСТИ ----------------
def load_activity():
    if ACTIVITY_FILE.exists():
        try:
            content = ACTIVITY_FILE.read_text(encoding="utf-8")
            if content:
                return json.loads(content)
            log.warning(f"Файл активности {ACTIVITY_FILE} пуст.")
        except json.JSONDecodeError as e:
            log.error(f"Ошибка декодирования JSON в файле активности: {e}")
        except Exception as e:
            log.error(f"Ошибка чтения файла активности: {e}")
    return {}


def save_activity():
    try:
        ACTIVITY_FILE.write_text(json.dumps(last_activity), encoding="utf-8")
    except Exception as e:
        log.error(f"Ошибка сохранения файла активности: {e}")


last_activity = load_activity()  # { "chat_id": timestamp }


# ---------------- ЯЗЫК ----------------
DEFAULT_LANG = "ru"
LANGS = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English"}


def load_lang() -> dict:
    try:
        if LANG_FILE.exists():
            return json.loads(LANG_FILE.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        log.error(f"Ошибка чтения файла языков: {e}")
    return {}


def save_lang():
    try:
        LANG_FILE.write_text(json.dumps(user_lang, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.error(f"Ошибка сохранения файла языков: {e}")


user_lang = load_lang()  # { "chat_id": "ru" | "en" }


def get_lang(chat_id: int) -> str:
    return user_lang.get(str(chat_id), DEFAULT_LANG)


def set_lang(chat_id: int, lang: str):
    user_lang[str(chat_id)] = lang
    save_lang()


# ---------------- ТЕКСТЫ (RU) ----------------
# Структура: главное меню -> раздел -> вопрос (ответ).
# Чтобы поправить ответ, меняйте текст в ANSWERS. Чтобы добавить вопрос —
# добавьте его в ANSWERS и допишите id в нужный раздел в SECTIONS.

MENU_TEXT = """👋 Здравствуйте! Я помощник поддержки.

Выберите тему, с которой нужна помощь 👇
Если ответа не нашлось — нажмите «Оператор»."""

OPERATOR_TEXT = """👨‍💻 Вызов оператора

Ваш запрос отправлен. Пожалуйста, подождите, оператор ответит вам в ближайшее время.

✍️ Пока вы ожидаете, опишите свой вопрос прямо здесь, в чате — так оператор сразу увидит его и сможет дать ответ, как только подключится 🙌
"""

# id раздела -> [id вопросов]  (названия кнопок разделов — в SECTION_TITLES)
SECTIONS = {
    "start":   ["connect", "add_device"],
    "tariffs": ["price", "devices", "unlimit", "tickets", "whitelist"],
    "pay":     ["where_pay", "pay_fail", "double_charge", "inactive", "extend"],
    "manage":  ["cancel", "change_card"],
    "broken":  ["nothing", "server", "limit_error", "reissue_failed"],
    "account": ["tg_login"],
}

SECTION_TITLES = {
    "ru": {
        "start": "🚀 Начало работы",
        "tariffs": "💎 Тарифы",
        "pay": "💳 Оплата",
        "manage": "⚙️ Подписка",
        "broken": "❗ Не работает",
        "account": "👤 Аккаунт",
    },
    "en": {
        "start": "🚀 Getting started",
        "tariffs": "💎 Pricing",
        "pay": "💳 Payment",
        "manage": "⚙️ Subscription",
        "broken": "❗ Not working",
        "account": "👤 Account",
    },
}

ANSWERS = {
    # ---------- 1. Начало работы ----------
    "connect": {
        "q": "Как подключить?",
        "images": ["vpn1.jpg", "vpn4.jpg", "vpn5.jpg", "vpn6.jpg"],
        "text": """🔐 Как подключить VPN

1️⃣ Перейдите в раздел «Оплата» и выберите подходящий тариф.
2️⃣ После успешной оплаты в разделе «Меню» появится пункт «Подключить VPN».
3️⃣ Войдите в приложение по вашей почте


""",
    },

    "add_device": {
        "q": "Как подключить второе устройство?",
        "images": ["limit1.jpg", "limit2.jpg", "limit3.jpg", "limit4.jpg"],
        "text": """➕ Как подключить дополнительное устройство

Если у вас есть ещё одно устройство (второй телефон, планшет, ноутбук), его нужно сначала оплатить, а затем подключить:

1️⃣ Перейдите в раздел «Оплата» в профиле.
2️⃣ Выберите «+1 устройство» и завершите оплату. Лимит увеличится автоматически в течение нескольких минут.
3️⃣ Откройте «Подключить VPN» и скопируйте вашу персональную ссылку.
4️⃣ Откройте её на дополнительном устройстве (или отправьте себе, например в «Избранное») и выполните подключение 📲

После чего можете подключить купленное устройство по тому же алгоритму, что и первое

❗️ Не покупайте устройство с опцией «Анонимно».
❗️ Нужна активная подписка.
""",
    },

    # ---------- 2. Тарифы и условия ----------
    "price": {
        "q": "Сколько стоит?",
        "images": ["ustr.jpg"],
        "text": """💰 Сколько стоит

📅 Подписка:
• 1 месяц — 399 ₽
• 3 месяца — 942 ₽
• 6 месяцев — 1 675 ₽
• 1 год — 2 513 ₽ (по промо первый год всего 2 010,40 ₽, затем 2 513 ₽ в год)

➕ Дополнительно:
• +1 устройство — 720 ₽ (навсегда)
• +5 устройств — 2199 ₽ (навсегда)
• +100 ГБ трафика — 250 ₽
• Безлимит трафика — 900 ₽ (навсегда)

💳 Оплата картой или через кошелёк, в любой валюте.

❗️ Подписка оплачивается отдельно. Устройства, ГБ и Безлимит её не заменяют.
""",
    },
    "devices": {
        "q": "Сколько устройств?",
        "images": ["ustr.jpg"],
        "text": """📱 Сколько устройств

1️⃣ В подписку входит 3 устройства, вы можете подключить Телефон, ПК, Телевизор и прочие устройства, на которые можно поставить VPN

2️⃣ Дополнительное устройство ➕ — 720 ₽, навсегда. Его нужно сначала оплатить, а затем подключить. Как это сделать — в разделе «Начало работы», вопрос «Как подключить второе устройство?».

❗️ Не покупайте устройство с опцией «Анонимно».
❗️ Для устройств нужна активная подписка.

Если видите ошибку про лимит устройств — смотрите раздел «Не работает соединение», вопрос «Ошибка: лимит устройств».
""",
    },
    "unlimit": {
        "q": "Что такое безлимит?",
        "images": ["tr1.jpg", "tr2.jpg", "tr3.jpg", "tr4.jpg"],
        "text": """📊 Трафик и Безлимит

🔹 В тариф входит 100 ГБ интернет-трафика на 30 дней.
📅 Трафик действует 30 дней с момента оплаты.
🔄 Обновление происходит автоматически ровно через месяц после даты оплаты
(например, оплатили 5 марта — обновится 5 апреля).

♾ Безлимит — это безлимитный интернет-трафик без ограничения по ГБ (900 ₽, навсегда).

➕ Если трафика не хватает, можно докупить Безлимит или дополнительный пакет ГБ (+100 ГБ — 250 ₽).

⚠️ Подписка оплачивается отдельно и должна быть активной всегда. Даже если вы купили Безлимит или ГБ, подписку всё равно нужно продлевать. Без активной подписки трафик (в том числе Безлимит) работать не будет.
""",
    },
    "tickets": {
        "q": "Что такое Билеты?",
        "images": ["podkl.jpg"],
        "text": """🎟 Что такое Билеты

Билеты — это механика, которая позволяет участвовать в розыгрыше.

🍀 Чем больше билетов, тем выше шанс выиграть.
⚙️ Билеты списываются автоматически при розыгрышах.
""",
    },
    # TODO: уточните у владельца, как именно работает VPN при белых списках, и поправьте текст.
    "whitelist": {
        "q": "Белые списки работают?",
        "images": ["podkl.jpg"],
        "text": """📶 Белые списки

Белые списки — это ограничение мобильного интернета, при котором открываются только разрешённые сайты. В такие периоды подключение может работать нестабильно.

Что можно попробовать:
1️⃣ Обновить подписку после переключения с WI-FI на Мобильный интернет
2️⃣ Переключиться на сервера с пометкой LTE
3️⃣ Проверить пинг серверов и подключится к самому быстрому

Если не помогло — нажмите «Оператор» и напишите, какой у вас оператор связи и в каком регионе вы находитесь.
""",
    },

    # ---------- 3. Оплата ----------
    "where_pay": {
        "q": "Где оплачивать?",
        "images": ["vpn1.jpg", "vpn2.jpg", "vpn3.jpg"],
        "text": """💳 Где оплачивать

1️⃣ Откройте профиль в боте и нажмите «ОПЛАТА».
2️⃣ Нажмите «Оплатить подписку».
3️⃣ В открывшемся окне оплаты выберите период (1, 3, 6 месяцев или год) и способ — «Карта» или «Кошелёк».
4️⃣ Завершите оплату — после этого появится кнопка «Подключить VPN» 📲
""",
    },
    "pay_fail": {
        "q": "Не получается оплатить, что делать?",
        "images": ["uprav_sub.jpg"],
        "text": """⚠️ Не получается оплатить

Попробуйте по порядку:

1️⃣ Проверьте, что на карте достаточно средств и разрешены онлайн-платежи.
2️⃣ Включите/выключите ВПН, в зависимости от состояния, после чего проведите оплату.
3️⃣ Выберите другой способ — «Резерв» или оплату Криптовалютой вместо основного способа.


Если не получилось — нажмите «Оператор» и пришлите скриншот ошибки.
""",
    },
    "double_charge": {
        "q": "Дважды списалась оплата",
        "images": ["uprav_sub.jpg"],
        "text": """💳 Дважды списалась оплата

Не волнуйтесь, мы разберёмся.

1️⃣ Не оплачивайте повторно.
2️⃣ Проверьте в приложении банка: иногда вторая сумма — это временная блокировка средств, которая снимается автоматически.
3️⃣ Если списание действительно прошло дважды, напишите нам в этот чат и пришлите:
• скриншоты списаний из банка (дата, время, сумма);
• способ оплаты (карта или кошелёк);
• ваш ID из профиля в боте.

Оператор проверит платежи и подскажет, как решить вопрос 🙌
""",
    },
    "inactive": {
        "q": "Купил, показывает неактивна",
        "images": ["uprav_sub.jpg"],
        "text": """❓ Купил, а подписка неактивна

1️⃣ Подождите несколько минут — активация проходит автоматически.
2️⃣ Напишите /menu в боте, чтобы данные обновились.
3️⃣ Убедитесь, что оплата прошла: списание должно быть видно в банке.

Когда подписка станет активной, в профиле появится кнопка «Подключить VPN» ✅

Если статус не изменился — нажмите «Оператор» и пришлите скриншот оплаты и ваш ID из профиля.
""",
    },
    "extend": {
        "q": "Как продлить оплату?",
        "images": ["vpn1.jpg", "vpn2.jpg", "vpn3.jpg"],
        "text": """🔄 Как продлить подписку

Подписка продлевается автоматически, дополнительных действий не требуется. Если хотите продлить подписку на больший срок, то купите её в боте

❗️ Подписка оплачивается отдельно и должна быть активной всегда. Покупка ГБ, Безлимита или устройств не заменяет продление подписки.
""",
    },

    # ---------- 4. Управление подпиской ----------
    # TODO: проверьте шаги по Tribute (названия кнопок) и условие про доступ до конца периода.
    "cancel": {
        "q": "Как отключить автопродление?",
        "images": ["uprav_sub.jpg"],
        "text": """🛑 Как отключить подписку и автопродление

Оплата подписки проходит через сервис Tribute, поэтому и отключается она там:

1️⃣ Откройте Tribute в Telegram (через него вы оплачивали подписку).
2️⃣ Перейдите в раздел со своими подписками.
3️⃣ Выберите «Premium VolnaLink VPN» и отмените подписку.

После отмены новые списания прекратятся. Доступ сохраняется до конца уже оплаченного периода.

Не получилось — нажмите «Оператор».
""",
    },
    # TODO: проверьте, можно ли менять карту в Tribute без повторного оформления подписки.
    "change_card": {
        "q": "Как сменить карту для оплаты?",
        "images": ["uprav_sub.jpg"],
        "text": """💳 Как сменить карту для оплаты подписки

Способ оплаты подписки хранится в Tribute:

1️⃣ Откройте Tribute в Telegram.
2️⃣ Перейдите в раздел со своими подписками и выберите «Premium VolnaLink VPN».
3️⃣ Замените способ оплаты или добавьте новую карту («Добавить карту»).

Для подписки по СБП нужно отменить текущую подписку и оформить с новой карты, чтобы последующие списания проходили по ней.

Если нужной кнопки нет — нажмите «Оператор», подскажем, как поступить.
""",
    },

    # ---------- 5. Не работает соединение ----------
    "nothing": {
        "q": "Не работает, ничего не делал",
        "images": ["ref1.jpg", "ref2.jpg"],
        "text": """❗️ Если у вас не работает VPN, выполните следующие действия:

1️⃣ Включите ✈️ авиарежим на 10–15 секунд, затем выключите его.

2️⃣ 🔄 Перезагрузите телефон.

3️⃣ 🌐 Обновите подписку в приложении

4️⃣ 📲 Переустановите приложение VPN.

⸻

❗️ Если ничего из этого не помогло — сделайте перевыпуск подписки.
Перевыпуск заменит вашу ссылку для подключения на новую — это часто решает проблему ✅

♻️ Меню → Управление устройствами → Перевыпустить подписку

После этого:
🔄 заново подключите VPN по новой ссылке

""",
    },
    "server": {
        "q": "Не работает какой-то сервер",
        "images": ["podkl.jpg"],
        "text": """🌍 Не работает один из серверов

1️⃣ Выберите в приложении другой сервер — один из них может быть временно недоступен.
2️⃣ Обновите подписку в приложении, чтобы список серверов обновился, и перезапустите приложение.


Если сервер всё равно не работает — нажмите «Оператор» и напишите:
• название сервера, который не работает;
• что вы меняли или обновляли перед этим.
""",
    },
    "limit_error": {
        "q": "Ошибка: лимит устройств",
        "images": ["ref1.jpg", "ref2.jpg"],
        "text": """❗️ Лимит устройств

Такая ошибка бывает в двух случаях:

1️⃣ Не оплачено дополнительное устройство
→ «Оплата» → «+1 устройство»

2️⃣ Вы подключаете новое устройство вместо старого
→ удалите старое: «Меню» → «Управление устройствами»

Возможно у вас одно устройство подключено к подписке дважды, перевыпустите подписку ♻️

После этого всё заработает ✅
""",
    },
    "reissue_failed": {
        "q": "Переустановил и перевыпустил — не помогло",
        "images": ["ustr.jpg"],
        "text": """🛠 Давайте разберёмся детальнее

Напишите, пожалуйста, в этот чат:

📱 1. Устройство — модель вашего телефона или компьютера.
📲 2. Приложение — через какое приложение вы подключаетесь.
🔢 3. Версию приложения.
⚙️ 4. Версию устройства — версию системы (например, iOS 18 или Android 14).
🎥 5. Видео проблемы — запишите экран: как вы подключаетесь и что происходит.

Как только пришлёте — оператор посмотрит и поможет 🙌
""",
    },

    # ---------- 6. Аккаунт и Telegram ----------
    "tg_login": {
        "q": "Не могу зайти в Telegram / не приходит код",
        "images": ["ustr.jpg"],
        "text": """👤 Не получается войти в Telegram или аккаунт

1️⃣ Код приходит в приложение Telegram на другом устройстве, где вы уже вошли (чат «Telegram»). 
2️⃣ Подождите несколько минут и не запрашивайте код слишком часто — Telegram может временно ограничить отправку.
3️⃣ Проверьте интернет: попробуйте Wi-Fi вместо мобильной сети (или наоборот), с включённым VPN и без него.

Если не приходит код с почты, посмотрите в Спаме. Если и там нет, то внимательно перепроверьте написание почты, это самая частая причина по которой не приходит код


Если ничего не помогло — нажмите «Оператор» и напишите, на каком шаге возникает проблема и какую ошибку вы видите.
""",
    },

    # ---------- Отдельная плитка в главном меню (не входит в разделы) ----------
    "reset": {
        "q": "Перевыпуск подписки",
        "images": ["ref1.jpg", "ref2.jpg"],
        "text": """♻️ Перевыпуск подписки

❓ Зачем нужен перевыпуск:
Перевыпуск меняет вашу ссылку для подключения на новую. Это помогает, если VPN перестал работать или старая ссылка больше не действует 🔗

Меню → Управление устройствами → Перевыпустить подписку

После этого:

🔄 заново подключите VPN по новой ссылке

📲 переустановите приложение
""",
    },
}

# ---------------- ТЕКСТЫ И КАРТИНКИ (EN) ----------------
# Для английской версии картинки лежат в img/ с суффиксом _en
# (например, vpn1_en.jpg). Если такого файла нет — бот покажет
# русскую картинку, поэтому можно добавлять их постепенно.
# Чего нет в переводе (текст, вопрос) — берётся из русской версии.
ANSWERS_I18N = {
    "en": {
        "connect": {
            "q": "How to connect?",
            "images": ["vpn1_en.jpg", "vpn4_en.jpg", "vpn5_en.jpg", "vpn6_en.jpg"],
            "text": """🔐 How to connect the VPN

1️⃣ Go to "Payment" and choose a suitable plan.
2️⃣ After a successful payment, the "Connect VPN" item will appear in the "Menu".
3️⃣ Log in to the app using your email.
""",
        },
        "add_device": {
            "q": "How to connect a second device?",
            "images": ["limit1_en.jpg", "limit2_en.jpg", "limit3_en.jpg", "limit4_en.jpg"],
            "text": """➕ How to connect an additional device

If you have another device (second phone, tablet, laptop), you need to pay for it first and then connect it:

1️⃣ Go to "Payment" in your profile.
2️⃣ Choose "+1 device" and complete the payment. The limit will increase automatically within a few minutes.
3️⃣ Open "Connect VPN" and copy your personal link.
4️⃣ Open it on the additional device (or send it to yourself, e.g. to "Saved Messages") and connect 📲

After that, you can connect the purchased device the same way as the first one.

❗️ Do not buy a device with the "Anonymous" option.
❗️ An active subscription is required.
""",
        },
        "price": {
            "q": "How much does it cost?",
            "images": ["ustr_en.jpg"],
            "text": """💰 Pricing

📅 Subscription:
• 1 month — 399 ₽
• 3 months — 942 ₽
• 6 months — 1,675 ₽
• 1 year — 2,513 ₽ (promo: first year only 2,010.40 ₽, then 2,513 ₽ per year)

➕ Extras:
• +1 device — 720 ₽ (forever)
• +5 devices — 2,199 ₽ (forever)
• +100 GB traffic — 250 ₽
• Unlimited traffic — 900 ₽ (forever)

💳 Pay by card or wallet, in any currency.

❗️ The subscription is paid separately. Devices, GB and Unlimited do not replace it.
""",
        },
        "devices": {
            "q": "How many devices?",
            "images": ["ustr_en.jpg"],
            "text": """📱 How many devices

1️⃣ The subscription includes 3 devices. You can connect a phone, PC, TV and any other device that supports a VPN app.

2️⃣ An additional device ➕ costs 720 ₽, forever. Pay for it first, then connect it. See "Getting started" → "How to connect a second device?".

❗️ Do not buy a device with the "Anonymous" option.
❗️ Devices require an active subscription.

If you see a device limit error, see "Not working" → "Error: device limit".
""",
        },
        "unlimit": {
            "q": "What is Unlimited?",
            "images": ["tr1_en.jpg", "tr2_en.jpg", "tr3_en.jpg", "tr4_en.jpg"],
            "text": """📊 Traffic and Unlimited

🔹 The plan includes 100 GB of traffic for 30 days.
📅 Traffic is valid for 30 days from the payment date.
🔄 It renews automatically exactly one month after the payment date
(e.g. paid on March 5 — renews on April 5).

♾ Unlimited is unlimited internet traffic with no GB cap (900 ₽, forever).

➕ If you run out of traffic, you can buy Unlimited or an extra GB pack (+100 GB — 250 ₽).

⚠️ The subscription is paid separately and must always be active. Even if you bought Unlimited or GB, you still need to renew the subscription. Without an active subscription, traffic (including Unlimited) will not work.
""",
        },
        "tickets": {
            "q": "What are Tickets?",
            "images": ["podkl_en.jpg"],
            "text": """🎟 What are Tickets

Tickets let you take part in giveaways.

🍀 The more tickets you have, the higher your chance to win.
⚙️ Tickets are spent automatically during giveaways.
""",
        },
        "whitelist": {
            "q": "Do whitelists work?",
            "images": ["podkl_en.jpg"],
            "text": """📶 Whitelists

Whitelists are mobile internet restrictions where only approved sites open. During such periods the connection may be unstable.

What you can try:
1️⃣ Update the subscription after switching from Wi-Fi to mobile data.
2️⃣ Switch to servers marked LTE.
3️⃣ Check the servers' ping and connect to the fastest one.

If it doesn't help, press "Operator" and tell us your mobile carrier and region.
""",
        },
        "where_pay": {
            "q": "Where to pay?",
            "images": ["vpn1_en.jpg", "vpn2_en.jpg", "vpn3_en.jpg"],
            "text": """💳 Where to pay

1️⃣ Open your profile in the bot and tap "PAYMENT".
2️⃣ Tap "Pay for subscription".
3️⃣ In the payment window choose a period (1, 3, 6 months or a year) and a method — "Card" or "Wallet".
4️⃣ Complete the payment — the "Connect VPN" button will then appear 📲
""",
        },
        "pay_fail": {
            "q": "Can't pay, what to do?",
            "images": ["uprav_sub_en.jpg"],
            "text": """⚠️ Can't pay

Try in order:

1️⃣ Make sure the card has enough funds and online payments are allowed.
2️⃣ Turn the VPN on or off (depending on its current state), then make the payment.
3️⃣ Choose another method — "Reserve" or cryptocurrency instead of the main method.


If it still fails, press "Operator" and send a screenshot of the error.
""",
        },
        "double_charge": {
            "q": "Charged twice",
            "images": ["uprav_sub_en.jpg"],
            "text": """💳 Charged twice

Don't worry, we'll sort it out.

1️⃣ Do not pay again.
2️⃣ Check your bank app: sometimes the second amount is a temporary hold that is released automatically.
3️⃣ If you were really charged twice, write to us in this chat and send:
• screenshots of the charges from the bank (date, time, amount);
• payment method (card or wallet);
• your ID from the profile in the bot.

The operator will check the payments and tell you how to resolve it 🙌
""",
        },
        "inactive": {
            "q": "I paid but it shows inactive",
            "images": ["uprav_sub_en.jpg"],
            "text": """❓ I paid but the subscription is inactive

1️⃣ Wait a few minutes — activation is automatic.
2️⃣ Send /menu in the bot to refresh the data.
3️⃣ Make sure the payment went through: the charge should be visible in your bank.

Once the subscription is active, the "Connect VPN" button will appear in your profile ✅

If the status hasn't changed, press "Operator" and send a screenshot of the payment and your ID from the profile.
""",
        },
        "extend": {
            "q": "How to renew?",
            "images": ["vpn1_en.jpg", "vpn2_en.jpg", "vpn3_en.jpg"],
            "text": """🔄 How to renew the subscription

The subscription renews automatically, no extra action is required. If you want to extend it for a longer period, buy it in the bot.

❗️ The subscription is paid separately and must always be active. Buying GB, Unlimited or devices does not replace renewing the subscription.
""",
        },
        "cancel": {
            "q": "How to cancel auto-renewal?",
            "images": ["uprav_sub_en.jpg"],
            "text": """🛑 How to cancel the subscription and auto-renewal

Subscription payments go through Tribute, so you cancel it there:

1️⃣ Open Tribute in Telegram (you paid through it).
2️⃣ Go to the section with your subscriptions.
3️⃣ Choose "Premium VolnaLink VPN" and cancel the subscription.

After cancelling, no new charges will be made. Access remains until the end of the already paid period.

Didn't work? Press "Operator".
""",
        },
        "change_card": {
            "q": "How to change the payment card?",
            "images": ["uprav_sub_en.jpg"],
            "text": """💳 How to change the card for subscription payments

The payment method is stored in Tribute:

1️⃣ Open Tribute in Telegram.
2️⃣ Go to your subscriptions and choose "Premium VolnaLink VPN".
3️⃣ Replace the payment method or add a new card ("Add card").

For an SBP subscription, you need to cancel the current subscription and sign up again with the new card, so that later charges go to it.

If you can't find the button, press "Operator" and we'll advise.
""",
        },
        "nothing": {
            "q": "Not working, I changed nothing",
            "images": ["ref1_en.jpg", "ref2_en.jpg"],
            "text": """❗️ If your VPN doesn't work, do the following:

1️⃣ Turn on ✈️ airplane mode for 10–15 seconds, then turn it off.

2️⃣ 🔄 Restart your phone.

3️⃣ 🌐 Update the subscription in the app.

4️⃣ 📲 Reinstall the VPN app.

⸻

❗️ If none of this helped, re-issue the subscription.
Re-issuing replaces your connection link with a new one — this often fixes the problem ✅

♻️ Menu → Device management → Re-issue subscription

After that:
🔄 connect the VPN again using the new link

""",
        },
        "server": {
            "q": "A server doesn't work",
            "images": ["podkl_en.jpg"],
            "text": """🌍 One of the servers doesn't work

1️⃣ Choose another server in the app — one of them may be temporarily unavailable.
2️⃣ Update the subscription in the app to refresh the server list, and restart the app.


If the server still doesn't work, press "Operator" and write:
• the name of the server that doesn't work;
• what you changed or updated before that.
""",
        },
        "limit_error": {
            "q": "Error: device limit",
            "images": ["ref1_en.jpg", "ref2_en.jpg"],
            "text": """❗️ Device limit

This error happens in two cases:

1️⃣ The additional device is not paid for
→ "Payment" → "+1 device"

2️⃣ You are connecting a new device instead of an old one
→ remove the old one: "Menu" → "Device management"

You may also have one device connected to the subscription twice — re-issue the subscription ♻️

After that everything will work ✅
""",
        },
        "reissue_failed": {
            "q": "Reinstalled and re-issued — no help",
            "images": ["ustr_en.jpg"],
            "text": """🛠 Let's look into it in more detail

Please write in this chat:

📱 1. Device — model of your phone or computer.
📲 2. App — which app you use to connect.
🔢 3. App version.
⚙️ 4. System version (e.g. iOS 18 or Android 14).
🎥 5. A video of the problem — record your screen: how you connect and what happens.

As soon as you send it, the operator will take a look and help 🙌
""",
        },
        "tg_login": {
            "q": "Can't log in to Telegram / no code",
            "images": ["ustr_en.jpg"],
            "text": """👤 Can't log in to Telegram or your account

1️⃣ The code arrives in the Telegram app on another device where you're already logged in (the "Telegram" chat).
2️⃣ Wait a few minutes and don't request the code too often — Telegram may temporarily limit sending.
3️⃣ Check your internet: try Wi-Fi instead of mobile data (or vice versa), with and without VPN.

If the code doesn't arrive by email, check your Spam folder. If it's not there either, carefully double-check the spelling of your email — this is the most common reason the code doesn't arrive.


If nothing helped, press "Operator" and tell us at which step the problem occurs and what error you see.
""",
        },
        "reset": {
            "q": "Re-issue subscription",
            "images": ["ref1_en.jpg", "ref2_en.jpg"],
            "text": """♻️ Re-issue subscription

❓ Why re-issue:
Re-issuing replaces your connection link with a new one. It helps if the VPN stopped working or the old link is no longer valid 🔗

Menu → Device management → Re-issue subscription

After that:

🔄 connect the VPN again using the new link

📲 reinstall the app
""",
        },
    }
}

# ---------------- ТЕКСТЫ ИНТЕРФЕЙСА ----------------
UI = {
    "ru": {
        "menu_text": MENU_TEXT,
        "operator_text": OPERATOR_TEXT,
        "pick_question": "Выберите вопрос 👇",
        "home": "🏠 Главное меню",
        "menu": "🏠 Меню",
        "back": "◀️ Назад",
        "operator": "👨‍💻 Оператор",
        "reset": "♻️ Перевыпуск",
        "language": "🌐 Язык / Language",
        "choose_lang": "🌐 Выберите язык / Choose language:",
    },
    "en": {
        "menu_text": """👋 Hello! I'm the support assistant.

Choose a topic you need help with 👇
If you can't find an answer, press "Operator".""",
        "operator_text": """👨‍💻 Operator call

Your request has been sent. Please wait, an operator will reply shortly.

✍️ While you wait, describe your question right here in the chat — the operator will see it right away and answer as soon as they connect 🙌
""",
        "pick_question": "Choose a question 👇",
        "home": "🏠 Main menu",
        "menu": "🏠 Menu",
        "back": "◀️ Back",
        "operator": "👨‍💻 Operator",
        "reset": "♻️ Re-issue",
        "language": "🌐 Язык / Language",
        "choose_lang": "🌐 Выберите язык / Choose language:",
    },
}

# Ответ -> раздел, в котором он лежит (для кнопки «назад»)
ANSWER_SECTION = {qid: sec for sec, qids in SECTIONS.items() for qid in qids}


# ---------------- ПОМОЩНИКИ ПЕРЕВОДА ----------------
def ui(chat_id: int, key: str) -> str:
    lang = get_lang(chat_id)
    return UI.get(lang, UI["ru"]).get(key) or UI["ru"][key]


def section_title(chat_id: int, sec: str) -> str:
    lang = get_lang(chat_id)
    return SECTION_TITLES.get(lang, {}).get(sec) or SECTION_TITLES["ru"][sec]


def answer_field(chat_id: int, qid: str, field: str) -> str:
    """field: 'q' или 'text'. Если перевода нет — русский оригинал."""
    lang = get_lang(chat_id)
    return ANSWERS_I18N.get(lang, {}).get(qid, {}).get(field) or ANSWERS[qid][field]


def answer_images(chat_id: int, qid: str) -> list:
    """Картинки для языка пользователя. Берём только существующие файлы;
    если для языка нет ни одной картинки — показываем русские."""
    lang = get_lang(chat_id)
    names = ANSWERS_I18N.get(lang, {}).get(qid, {}).get("images")
    if names:
        existing = [n for n in names if (IMG_DIR / n).exists()]
        if existing:
            return existing
    return ANSWERS[qid]["images"]


# ---------------- КЛАВИАТУРЫ ----------------
def grid(buttons: list, per_row: int = 2) -> list:
    """Разбивает список кнопок на ряды по per_row штук."""
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


def main_menu_kb(chat_id: int):
    items = [
        InlineKeyboardButton(text=section_title(chat_id, sec), callback_data=f"s:{sec}")
        for sec in SECTIONS
    ]
    items.append(InlineKeyboardButton(text=ui(chat_id, "reset"), callback_data="a:reset"))
    items.append(InlineKeyboardButton(text=ui(chat_id, "operator"), callback_data="operator"))

    rows = grid(items, 2)  # 8 кнопок -> 4 ряда по 2
    rows.append([InlineKeyboardButton(text=ui(chat_id, "language"), callback_data="lang")])  # во всю ширину
    return InlineKeyboardMarkup(inline_keyboard=rows)


def section_kb(chat_id: int, sec: str):
    # вопросы длинные, поэтому по одному в ряд
    rows = [
        [InlineKeyboardButton(text=answer_field(chat_id, q, "q"), callback_data=f"a:{q}")]
        for q in SECTIONS[sec]
    ]
    rows.append([
        InlineKeyboardButton(text=ui(chat_id, "home"), callback_data="menu"),
        InlineKeyboardButton(text=ui(chat_id, "operator"), callback_data="operator"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def answer_kb(chat_id: int, qid: str):
    sec = ANSWER_SECTION.get(qid)  # у «Перевыпуска» раздела нет — только «Меню»
    nav = [InlineKeyboardButton(text=ui(chat_id, "menu"), callback_data="menu")]
    if sec:
        nav.insert(0, InlineKeyboardButton(text=ui(chat_id, "back"), callback_data=f"s:{sec}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text=ui(chat_id, "operator"), callback_data="operator")],
    ])


def language_kb(chat_id: int):
    rows = grid(
        [InlineKeyboardButton(text=name, callback_data=f"lang:{code}") for code, name in LANGS.items()],
        2,
    )
    rows.append([InlineKeyboardButton(text=ui(chat_id, "home"), callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------- ЯДРО ЛОГИКИ ----------------
async def clear_old_messages(chat_id: int, biz_id: str = None):
    """Удаляет старые сообщения бота с использованием business_connection_id."""
    if chat_id in user_messages and user_messages[chat_id]:
        if not biz_id:
            biz_id = business_connections.get(chat_id)

        if biz_id:
            try:
                await bot.delete_business_messages(
                    business_connection_id=biz_id,
                    message_ids=user_messages[chat_id],
                )
            except TelegramBadRequest as e:
                log.warning(f"Ошибка при удалении бизнес-сообщений в чате {chat_id}: {e}")
            except Exception as e:
                log.error(f"Ошибка при удалении бизнес-сообщений в чате {chat_id}: {e}")
        else:
            for msg_id in user_messages[chat_id]:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except TelegramBadRequest:
                    pass
                except Exception as e:
                    log.error(f"Ошибка при удалении сообщения {msg_id} в чате {chat_id}: {e}")
        user_messages[chat_id] = []


async def send_screen(chat_id: int, text: str, keyboard: InlineKeyboardMarkup,
                      images=None, biz_id: str = None):
    """Очищает историю и присылает новый экран: картинки (если есть) + текст с кнопками."""
    if biz_id:
        business_connections[chat_id] = biz_id

    await clear_old_messages(chat_id, biz_id)
    user_messages.setdefault(chat_id, [])

    media = []
    for name in images or []:
        path = IMG_DIR / name
        if path.exists():
            media.append(InputMediaPhoto(media=FSInputFile(path)))

    if media:
        try:
            if len(media) == 1:
                msg = await bot.send_photo(chat_id, media[0].media, business_connection_id=biz_id)
                user_messages[chat_id].append(msg.message_id)
            else:
                msgs = await bot.send_media_group(chat_id, media, business_connection_id=biz_id)
                for m in msgs:
                    user_messages[chat_id].append(m.message_id)
        except Exception as e:
            log.error(f"Ошибка при отправке медиа: {e}")

    main_msg = await bot.send_message(
        chat_id,
        text,
        reply_markup=keyboard,
        business_connection_id=biz_id,
    )
    user_messages[chat_id].append(main_msg.message_id)


async def show_main_menu(chat_id: int, biz_id: str = None):
    await send_screen(chat_id, ui(chat_id, "menu_text"), main_menu_kb(chat_id), biz_id=biz_id)


async def show_section(chat_id: int, sec: str, biz_id: str = None):
    title = section_title(chat_id, sec)
    await send_screen(
        chat_id,
        f"{title}\n\n{ui(chat_id, 'pick_question')}",
        section_kb(chat_id, sec),
        biz_id=biz_id,
    )


async def show_answer(chat_id: int, qid: str, biz_id: str = None):
    await send_screen(
        chat_id,
        answer_field(chat_id, qid, "text"),
        answer_kb(chat_id, qid),
        answer_images(chat_id, qid),
        biz_id,
    )


async def show_language(chat_id: int, biz_id: str = None):
    await send_screen(chat_id, ui(chat_id, "choose_lang"), language_kb(chat_id), biz_id=biz_id)


async def call_operator(call: CallbackQuery, biz_id: str = None):
    chat_id = call.message.chat.id

    # Подтверждение клиенту (старые сообщения удаляются внутри)
    await send_screen(chat_id, ui(chat_id, "operator_text"), main_menu_kb(chat_id), biz_id=biz_id)

    # Уведомляем владельца бизнес-аккаунта (всегда на русском)
    if biz_id:
        try:
            conn = await bot.get_business_connection(biz_id)
            owner_id = conn.user.id
            user_name = call.message.chat.full_name or call.message.chat.username or str(chat_id)
            await bot.send_message(
                owner_id,
                f"🔔 Клиент <b>{user_name}</b> (ID: <code>{chat_id}</code>) вызвал оператора!",
                parse_mode="HTML",
            )
        except Exception as e:
            log.warning(f"Не удалось уведомить владельца: {e}")


def should_send_menu(chat_id: int) -> bool:
    """Проверяет, прошло ли 6 часов с последнего сообщения пользователя."""
    chat_id_str = str(chat_id)
    current_time = time.time()
    last_time = last_activity.get(chat_id_str, 0)

    last_activity[chat_id_str] = current_time
    save_activity()
    return current_time - last_time > COOLDOWN_6_HOURS


# ---------------- ОБРАБОТЧИКИ ----------------
# Когда пишут тебе в личку (Business)
@dp.business_message()
async def business_handler(message: Message):
    # Сообщение владельца аккаунта — игнорируем
    if message.from_user.id != message.chat.id:
        log.info(f"[business] сообщение владельца в чате {message.chat.id} — игнорирую")
        return

    business_connections[message.chat.id] = message.business_connection_id

    if should_send_menu(message.chat.id):
        log.info(f"[business] сообщение клиента {message.chat.id} — отправляю меню")
        await show_main_menu(message.chat.id, message.business_connection_id)
    else:
        log.info(f"[business] клиент {message.chat.id} писал меньше 6 часов назад — меню не нужно")


# Обработка кнопок
@dp.callback_query()
async def callback_handler(call: CallbackQuery):
    chat_id = call.message.chat.id
    data = call.data or ""

    biz_id = call.message.business_connection_id or business_connections.get(chat_id)

    try:
        if data == "operator":
            await call_operator(call, biz_id)
        elif data == "lang":
            await show_language(chat_id, biz_id)
        elif data.startswith("lang:") and data[5:] in LANGS:
            set_lang(chat_id, data[5:])
            await show_main_menu(chat_id, biz_id)
        elif data.startswith("s:") and data[2:] in SECTIONS:
            await show_section(chat_id, data[2:], biz_id)
        elif data.startswith("a:") and data[2:] in ANSWERS:
            await show_answer(chat_id, data[2:], biz_id)
        else:
            # "menu" и устаревшие кнопки из старых сообщений — на главное меню
            await show_main_menu(chat_id, biz_id)
    except Exception as e:
        log.error(f"Ошибка обработки кнопки {data!r} в чате {chat_id}: {e}")

    try:
        await call.answer()
    except Exception:
        pass


# ---------------- ЗАПУСК ----------------
async def main():
    print("🚀 Бот запущен.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass