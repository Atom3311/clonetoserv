"""Мелкие помощники: время, даты, деньги и валюты, разбор сумм и дат."""
from __future__ import annotations

import calendar
import html
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import config

MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август",
              "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
               "сентябре", "октябре", "ноябре", "декабре"]
WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
WD_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€", "GBP": "£", "KZT": "₸", "UAH": "₴", "CNY": "¥"}

_TZ = None
_NOW_OVERRIDE = None


def tz() -> ZoneInfo:
    global _TZ
    if _TZ is None or _TZ.key != config.TZ:
        _TZ = ZoneInfo(config.TZ)
    return _TZ


def set_now(dt) -> None:
    """Только для тестов: зафиксировать «сейчас». set_now(None) возвращает настоящие часы."""
    global _NOW_OVERRIDE
    _NOW_OVERRIDE = dt


def now() -> datetime:
    """Текущее местное время (без часового пояса, секунды обнулены)."""
    if _NOW_OVERRIDE is not None:
        return _NOW_OVERRIDE.replace(second=0, microsecond=0)
    return datetime.now(tz()).replace(tzinfo=None, second=0, microsecond=0)


def today() -> date:
    return now().date()


def stamp() -> str:
    n = now()
    return n.isoformat(sep=" ", timespec="minutes")


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def base_cur() -> str:
    return config.BASE_CURRENCY


# ---------- числа и деньги ----------

def num(x) -> str:
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        s = f"{int(round(x)):,}".replace(",", " ")
    else:
        s = f"{x:,.2f}".replace(",", " ").replace(".", ",")
    return s


def money(x, cur: str | None = None) -> str:
    cur = (cur or base_cur()).upper()
    return f"{num(x)} {SYMBOLS.get(cur, cur)}"


def money0(x, cur: str | None = None) -> str:
    """Без копеек — для «≈ в день», «≈ в месяц»."""
    return money(round(float(x)), cur)


def money_pair(orig: float, cur: str, base_amount: float) -> str:
    """«20 $ (≈ 1 900 ₽)» для чужой валюты, «300 ₽» для своей."""
    cur = (cur or base_cur()).upper()
    if cur == base_cur():
        return money(base_amount)
    return f"{money(orig, cur)} (≈ {money(base_amount)})"


def signed(x) -> str:
    x = round(float(x), 2)
    if x > 0:
        return "+" + money(x)
    if x < 0:
        return "−" + money(-x)
    return money(0)


def bar(frac: float, width: int = 10) -> str:
    frac = max(0.0, min(1.0, frac))
    f = int(round(frac * width))
    return "█" * f + "░" * (width - f)


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def days_phrase(n: int) -> str:
    if n == 0:
        return "сегодня"
    if n == 1:
        return "завтра"
    if n == -1:
        return "вчера"
    if n > 0:
        return f"через {n} {plural(n, 'день', 'дня', 'дней')}"
    return f"{-n} {plural(-n, 'день', 'дня', 'дней')} назад"


# ---------- валюты и суммы ----------

_CUR_EXACT = {
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "₽": "RUB", "rub": "RUB", "rur": "RUB",
    "р": "RUB", "£": "GBP", "gbp": "GBP", "¥": "CNY", "cny": "CNY", "₸": "KZT", "kzt": "KZT",
    "₴": "UAH", "uah": "UAH", "тг": "KZT", "byn": "BYN", "try": "TRY", "aed": "AED",
}
_CUR_PREFIX = [
    ("долл", "USD"), ("бакс", "USD"), ("доллар", "USD"), ("евро", "EUR"), ("руб", "RUB"),
    ("тенге", "KZT"), ("грн", "UAH"), ("гривн", "UAH"), ("фунт", "GBP"), ("юан", "CNY"),
    ("лир", "TRY"), ("дирхам", "AED"),
]


def norm_currency(s) -> str | None:
    t = str(s or "").strip().casefold().strip(".,; ")
    if not t:
        return None
    if t in _CUR_EXACT:
        return _CUR_EXACT[t]
    for pre, code in _CUR_PREFIX:
        if t.startswith(pre):
            return code
    if re.fullmatch(r"[a-z]{3}", t):
        return t.upper()
    return None


_SYMS = "$€₽£¥₸₴"
_MONEY = re.compile(
    r"^\s*(?P<sym>[" + _SYMS + r"])?\s*(?P<num>\d[\d  ]*(?:[.,]\d+)?)\s*"
    r"(?P<mult>млн|тысяч[аи]?|тыс\.?|тыщ[аи]?|к|k)?\s*"
    r"(?P<cur>[" + _SYMS + r"A-Za-zА-Яа-яЁё.]{0,12})\s*$", re.I)


def parse_money(text) -> tuple[float, str | None] | None:
    """'300' -> (300, None); '20$' -> (20, 'USD'); '15 евро' -> (15, 'EUR'); '50к' -> (50000, None)."""
    m = _MONEY.match(str(text or ""))
    if not m:
        return None
    try:
        n = float(re.sub(r"[\s ]", "", m.group("num")).replace(",", "."))
    except ValueError:
        return None
    mult = (m.group("mult") or "").casefold()
    if mult == "млн":
        n *= 1_000_000
    elif mult:
        n *= 1000
    cur = None
    if m.group("sym"):
        cur = norm_currency(m.group("sym"))
    tail = m.group("cur")
    if tail:
        c2 = norm_currency(tail)
        if c2 is None:
            return None
        cur = c2
    if n <= 0:
        return None
    return n, cur


_LAST_WORDS = ("last", "последняя", "последнее", "последний", "прошлая")


def parse_human_date(text, ref: date | None = None) -> date | None:
    ref = ref or today()
    t = str(text or "").strip().casefold()
    if not t:
        return None
    words = {"сегодня": 0, "вчера": -1, "позавчера": -2, "завтра": 1, "послезавтра": 2}
    if t in words:
        return ref + timedelta(days=words[t])
    d = parse_date(t)
    if d:
        return d
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", t)
    if m:
        dd, mm = int(m.group(1)), int(m.group(2))
        yy = int(m.group(3)) if m.group(3) else ref.year
        if yy < 100:
            yy += 2000
        try:
            return date(yy, mm, dd)
        except ValueError:
            return None
    return None


# ---------- даты ----------

def last_day(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def occurrence(day: int, y: int, m: int) -> date:
    """Дата «каждое N-е число» в месяце (31-е в коротком месяце = последний день)."""
    return date(y, m, min(max(int(day), 1), last_day(y, m)))


def add_months(d: date, n: int) -> tuple[int, int]:
    idx = d.year * 12 + (d.month - 1) + n
    return idx // 12, idx % 12 + 1


_DT = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?")


def parse_dt(s) -> tuple[date, time | None] | None:
    if not s:
        return None
    m = _DT.match(str(s))
    if not m:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        t = time(int(m.group(4)), int(m.group(5))) if m.group(4) is not None else None
    except ValueError:
        return None
    if not (2000 <= d.year <= 2100):
        return None
    return d, t


def parse_date(s) -> date | None:
    r = parse_dt(s)
    return r[0] if r else None


def norm_due(s) -> str | None:
    r = parse_dt(s)
    if not r:
        return None
    d, t = r
    return f"{d.isoformat()} {t.strftime('%H:%M')}" if t else d.isoformat()


def remind_from(s, default_hour: int | None = None) -> str | None:
    """Строка даты/времени -> 'YYYY-MM-DD HH:MM'. Если времени нет — берём час по умолчанию."""
    r = parse_dt(s)
    if not r:
        return None
    d, t = r
    if t is None:
        t = time(config.DEFAULT_REMIND_HOUR if default_hour is None else default_hour, 0)
    return f"{d.isoformat()} {t.strftime('%H:%M')}"


def to_datetime(s) -> datetime | None:
    r = parse_dt(s)
    if not r:
        return None
    d, t = r
    return datetime.combine(d, t or time(0, 0))


def fmt_short(d: date) -> str:
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def fmt_date(d: date, ref: date | None = None) -> str:
    ref = ref or today()
    delta = (d - ref).days
    if delta == 0:
        return "сегодня"
    if delta == -1:
        return "вчера"
    if delta == 1:
        return "завтра"
    s = f"{WD_SHORT[d.weekday()]}, {fmt_short(d)}"
    if d.year != ref.year:
        s += f" {d.year}"
    return s


def fmt_due(s: str | None, ref: date | None = None) -> str:
    r = parse_dt(s)
    if not r:
        return ""
    d, t = r
    out = fmt_date(d, ref)
    if t:
        out += f", {t.strftime('%H:%M')}"
    return out


def period_range(name: str | None, ref: date | None = None) -> tuple[date, date, str]:
    ref = ref or today()
    name = (name or "month").lower()
    if name == "today":
        return ref, ref, "Сегодня"
    if name == "yesterday":
        y = ref - timedelta(days=1)
        return y, y, "Вчера"
    if name == "week":
        s = ref - timedelta(days=ref.weekday())
        return s, ref, f"Неделя {fmt_short(s)} – {fmt_short(ref)}"
    if name == "last_week":
        s = ref - timedelta(days=ref.weekday() + 7)
        e = s + timedelta(days=6)
        return s, e, f"Прошлая неделя {fmt_short(s)} – {fmt_short(e)}"
    if name == "last7":
        return ref - timedelta(days=6), ref, "Последние 7 дней"
    if name == "last30":
        return ref - timedelta(days=29), ref, "Последние 30 дней"
    if name == "last_month":
        y, m = add_months(ref, -1)
        return date(y, m, 1), date(y, m, last_day(y, m)), f"{MONTHS_NOM[m - 1]} {y}"
    if name == "year":
        return date(ref.year, 1, 1), ref, f"{ref.year} год"
    if name == "all":
        return date(2000, 1, 1), ref, "За всё время"
    return date(ref.year, ref.month, 1), ref, f"{MONTHS_NOM[ref.month - 1]} {ref.year}"


PERIODS = ("today", "yesterday", "week", "last_week", "last7", "last30", "month", "last_month", "year", "all")


# ---------- сравнение имён ----------

def fuzzy_eq(a: str, b: str) -> bool:
    """Грубое сравнение слов с учётом падежей: Вася/Васе/Васи, ноутбук/ноутбука."""
    a, b = a.casefold().strip(), b.casefold().strip()
    if not a or not b:
        return False
    if a == b:
        return True
    p = 0
    for x, y in zip(a, b):
        if x != y:
            break
        p += 1
    return p >= max(3, min(len(a), len(b)) - 2)


def tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", str(s).casefold()) if t]


def match_score(query: str, name: str) -> int:
    """Чем больше слов запроса нашлось в имени, тем выше. 100 — точное совпадение."""
    if not query or not name:
        return 0
    if query.casefold().strip() == name.casefold().strip():
        return 100
    nt = tokens(name)
    score = 0
    for q in tokens(query):
        if len(q) < 3 and q not in nt:
            continue
        if any(fuzzy_eq(q, t) for t in nt):
            score += 1
    return score


def same_person(a: str, b: str) -> bool:
    return match_score(a, b) > 0
