"""Быстрый разбор без ИИ: команды («баланс», «задачи»…) и простые записи вида «300 шаурма».

Это экономит бесплатные лимиты Groq и работает, даже если ИИ недоступен.
"""
from __future__ import annotations

import re

import util

PERIOD_WORDS = [
    (r"прошл\w+ недел\w+", "last_week"), (r"недел\w+", "week"), (r"сегодня", "today"), (r"вчера", "yesterday"),
    (r"прошл\w+ месяц\w*", "last_month"), (r"месяц\w*", "month"), (r"(за )?всё время|(за )?все время", "all"),
    (r"7 дн\w+", "last7"), (r"30 дн\w+", "last30"), (r"год\w*", "year"),
]


def find_period(s: str) -> str | None:
    for pat, p in PERIOD_WORDS:
        if re.search(pat, s):
            return p
    return None


def _q(what: str, **kw) -> list[dict]:
    return [{"type": "query", "what": what, **kw}]


def norm(text: str) -> str:
    t = text.strip().casefold()
    t = re.sub(r"^/", "", t)
    t = re.sub(r"@\w+bot\b", "", t).strip()
    t = re.sub(r"[?!.…]+$", "", t).strip()
    return re.sub(r"\s+", " ", t)


ONOFF = r"(вкл\w*|включи\w*|on|выкл\w*|выключи\w*|off)"


def _onoff(s: str) -> str:
    return "off" if s.startswith(("выкл", "off")) or s.startswith("выключи") else "on"


def route(text: str) -> list[dict] | None:
    """Вернёт список действий, если сообщение — точная команда. Иначе None."""
    t = norm(text)
    if not t:
        return None
    m = re.fullmatch(r"(?:утро|утренняя сводка|сводка)\s+" + ONOFF, t)
    if m:
        return [{"type": "settings", "key": "morning", "value": _onoff(m.group(1))}]
    m = re.fullmatch(r"(?:итоги недели|недельные итоги|итоги по неделям)\s+" + ONOFF, t)
    if m:
        return [{"type": "settings", "key": "weekly", "value": _onoff(m.group(1))}]
    m = re.fullmatch(r"курс\s+(\S+)\s+(авто|auto|\d[\d.,]*)", t)
    if m and util.norm_currency(m.group(1)):
        return [{"type": "rate", "currency": util.norm_currency(m.group(1)), "value": m.group(2)}]
    m = re.fullmatch(r"(?:верни|восстанови)\s+#?(\d+)", t)
    if m:
        return [{"type": "restore", "entity": "tx", "id": int(m.group(1))}]
    simple = {
        r"help|помощь|что ты умеешь|что умеешь|команды": "help",
        r"balance|баланс|сколько денег|сколько у меня денег|остаток": "balance",
        r"tasks|задачи|дела|список дел|что надо сделать|todo": "tasks",
        r"debts|долги": "debts",
        r"stashes|заначки?|копилки?|цели|мои цели": "stashes",
        r"subs|подписки|платежи|регулярные платежи|сроки|мои подписки": "subs",
        r"recent|последние|последние записи|список|история трат": "recent",
        r"history|что я менял|что менял|история изменений|изменения": "history",
        r"categories|категории": "categories",
        r"rates|курс|курсы|курс валют": "rates",
        r"settings|настройки": "settings",
        r"утро|что на сегодня|план на сегодня|brief": "brief",
        r"итоги недели|недельные итоги|weekly": "weekly",
    }
    for pat, what in simple.items():
        if re.fullmatch(pat, t):
            return _q(what)
    if re.fullmatch(r"undo|отмени|отмени последнее|отмени последнюю запись|отмени последнюю трату|отмени это", t):
        return [{"type": "undo"}]
    if re.fullmatch(r"backup|бэкап|бекап|скинь базу|выгрузи данные|экспорт", t):
        return [{"type": "backup"}]
    m = re.fullmatch(r"(?:stats|статистика|траты|расходы|итоги)(?:\s+(?:за |на )?(.+))?", t)
    if m:
        rest = m.group(1)
        if not rest:
            return _q("stats", period="month")
        p = find_period(rest)
        if p:
            return _q("stats", period=p)
    return None


# ---------- простые записи без ИИ ----------

KEYWORDS = {
    "Еда": ["шаурм", "кофе", "обед", "ужин", "завтрак", "продукт", "пицц", "бургер", "кафе", "столов", "перекус",
            "хлеб", "ролл", "суши", "макдон", "магазин", "пятёрочк", "пятерочк", "магнит", "ашан", "вода", "сок",
            "шоколад", "мороженое", "доставк", "булочк", "чай", "лапш", "бизнес-ланч"],
    "Транспорт": ["метро", "такси", "автобус", "маршрутк", "проезд", "бензин", "электричк", "самокат", "парковк",
                  "трамвай", "троллейбус"],
    "Связь": ["интернет", "инет", "телефон", "связь", "мтс", "билайн", "мегафон", "теле2"],
    "Подписки": ["подписк", "spotify", "спотифай", "netflix", "нетфликс", "youtube", "ютуб"],
    "Здоровье": ["аптек", "лекарств", "врач", "стоматолог", "анализ", "таблетк"],
    "Одежда": ["кроссовк", "куртк", "футболк", "одежд", "джинс", "обувь"],
    "Развлечения": ["кино", "игр", "клуб", "бар", "концерт", "стим", "steam"],
    "Учёба": ["книг", "учебник", "канцеляр", "ксерокс", "распечатк"],
    "Жильё": ["аренд", "квартир", "коммунал", "общаг"],
    "Подарки": ["подар"],
}
INCOME_KEYWORDS = {
    "Зарплата": ["зарплат", "аванс", "стипенд"],
    "Подработка": ["подработк", "фриланс", "заказ", "халтур"],
}
INCOME_HINT = re.compile(r"зарплат|аванс|стипенд|получил|пришл|перевели|заработал|подработк|фриланс|халтур", re.I)


def _kw_category(desc: str, table: dict) -> str | None:
    for tok in util.tokens(desc):
        for cat, stems in table.items():
            for st in stems:
                if (len(st) <= 3 and tok == st) or (len(st) > 3 and tok.startswith(st)):
                    return cat
    return None


def remembered_category(db, desc: str, kind: str) -> str | None:
    d = desc.casefold().strip()
    for r in db.q("SELECT description, category FROM tx WHERE deleted=0 AND kind=? AND description!='' "
                  "ORDER BY id DESC LIMIT 800", (kind,)):
        if r["description"].casefold().strip() == d:
            return r["category"]
    return None


def simple_tx(text: str, db, allow_unknown: bool = False) -> list[dict] | None:
    """«300 шаурма», «шаурма 300», «15 евро игра», «+50к зарплата». Категория: память → словарь → ИИ."""
    t = " ".join(text.strip().split())
    if not t or len(t) > 80:
        return None
    kind = "income" if t.startswith("+") else "expense"
    t = t.lstrip("+").strip()
    t = re.sub(r"^(потратил[аи]?|заплатил[аи]?|купил[аи]?|отдал[аи]?|потрачено|трата)\s+", "", t, flags=re.I)
    toks = t.split()
    amount = cur = desc = None
    for n in (3, 2, 1):
        if len(toks) > n:
            m = util.parse_money(" ".join(toks[:n]))
            if m:
                amount, cur = m
                desc = " ".join(toks[n:])
                break
    if amount is None:
        for n in (3, 2, 1):
            if len(toks) > n:
                m = util.parse_money(" ".join(toks[-n:]))
                if m:
                    amount, cur = m
                    desc = " ".join(toks[:-n])
                    break
    if amount is None or not desc:
        return None
    if amount < 10 and not cur:
        return None  # «2 кофе» — это не 2 рубля
    desc = re.sub(r"^(на|за|в)\s+", "", desc, flags=re.I).strip()
    if (not desc or re.search(r"\d", desc) or len(desc.split()) > 4 or re.search(r"[,;?]|\sи\s", desc)
            or desc.casefold().startswith(("напомни", "отложил", "одолжил", "занял", "отмени", "курс"))):
        return None
    if INCOME_HINT.search(desc) or INCOME_HINT.search(t):
        kind = "income"
    cat = remembered_category(db, desc, kind) or _kw_category(desc, INCOME_KEYWORDS if kind == "income" else KEYWORDS)
    if not cat:
        if not allow_unknown:
            return None
        cat = "Зарплата" if kind == "income" else "Другое"
    a = {"type": kind, "amount": amount, "category": cat, "description": desc}
    if cur:
        a["currency"] = cur
    return [a]
