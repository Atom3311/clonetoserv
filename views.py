"""Тексты для Telegram (HTML). Только форматирование — никакой записи в базу."""
from __future__ import annotations

import json
from datetime import date, timedelta

import calc
import config
import util
from util import bar, esc, money, money0, num

KIND_ICON = {"workout": "🏋️", "food": "🍎", "sleep": "😴", "weight": "⚖️", "mood": "🙂", "note": "📓"}


# ---------- строки ----------

def tx_line(r, emojis: dict, ref: date | None = None, with_id: bool = True) -> str:
    ref = ref or util.today()
    d = util.parse_date(r["date"]) or ref
    cur = r["currency"] or util.base_cur()
    amt = util.money_pair(r["orig_amount"], cur, r["amount"])
    em = emojis.get(r["category"], "🏷")
    sign = "+" if r["kind"] == "income" else ""
    desc = f" — {esc(r['description'])}" if r["description"] else ""
    when = "" if d == ref else f" ({util.fmt_date(d, ref)})"
    ident = f"#{r['id']} " if with_id else ""
    return f"{ident}{em} {esc(r['category'])} · {sign}{amt}{desc}{when}"


def _pct(a: float, b: float) -> str:
    if b <= 0:
        return ""
    d = (a - b) / b * 100
    if abs(d) < 1:
        return "≈ как раньше"
    return f"{'+' if d > 0 else '−'}{abs(d):.0f}%"


# ---------- баланс ----------

def balance_view(db, rates) -> str:
    ref = util.today()
    bal = calc.balance(db)
    lines = ["💰 <b>Баланс</b>", f"Свободно: <b>{money(bal)}</b>"]
    if db.setting("opening") is None and not db.one("SELECT 1 FROM tx WHERE deleted=0 LIMIT 1"):
        lines.append("<i>Скажи, сколько у тебя сейчас денег: «сейчас у меня 12000», — и я буду считать от этого.</i>")
    stash = calc.stash_total_value(db, rates)
    if stash:
        lines.append(f"🐷 В заначках: {money(stash)}")
    lent, borrowed = calc.debts_open(db)
    if lent or borrowed:
        parts = []
        if lent:
            parts.append(f"мне должны {money(lent)}")
        if borrowed:
            parts.append(f"я должен {money(borrowed)}")
        lines.append("💳 Долги: " + ", ".join(parts))
    a = calc.allowance(db, rates, ref)
    if a:
        lines.append("")
        lines.append(f"📅 До «{esc(a['next_name'])}» ({util.fmt_short(a['next'])}) — {util.days_phrase(a['days'])}")
        if a["upcoming"]:
            names = ", ".join(f"{esc(n)} {money(b)}" for n, b, _ in a["items"][:4])
            lines.append(f"Обязательные платежи до этого: {money(a['upcoming'])} ({names})")
        pd = a["per_day"]
        if pd >= 0:
            lines.append(f"✅ Можно тратить ≈ <b>{money0(pd)}</b> в день")
        else:
            lines.append(f"🔴 Не хватает {money(-pd * max(a['days'], 1))} до этой даты")
    total, bad = calc.sub_monthly_total(db, rates)
    if total:
        lines.append(f"🔁 Подписки и регулярные платежи: ≈ {money0(total)} в месяц")
    return "\n".join(lines)


# ---------- статистика ----------

def stats_view(db, rates, period: str = "month", category: str | None = None) -> str:
    ref = util.today()
    start, end, label = util.period_range(period, ref)
    emojis = db.emojis()
    if category:
        cat = db.find_category(category)
        cname = cat["name"] if cat else category
        rows = db.q("SELECT * FROM tx WHERE deleted=0 AND kind='expense' AND category=? AND date>=? AND date<=? "
                    "ORDER BY date DESC, id DESC", (cname, start.isoformat(), end.isoformat()))
        total = sum(r["amount"] for r in rows)
        if not rows:
            return f"{emojis.get(cname, '🏷')} {esc(cname)} — {label.lower()}: трат нет."
        out = [f"{emojis.get(cname, '🏷')} <b>{esc(cname)}</b> — {label.lower()}",
               f"Итого: <b>{money(total)}</b> ({len(rows)} {util.plural(len(rows), 'запись', 'записи', 'записей')})"]
        for r in rows[:12]:
            out.append("• " + tx_line(r, emojis, ref))
        if len(rows) > 12:
            out.append(f"…и ещё {len(rows) - 12}")
        return "\n".join(out)

    exp = calc.sum_tx(db, "expense", start, end)
    inc = calc.sum_tx(db, "income", start, end)
    lines = [f"📊 <b>{esc(label)}</b>"]
    if not exp and not inc:
        lines.append("Записей за этот период нет.")
        return "\n".join(lines)
    lines.append(f"💰 Доходы: {money(inc)}")
    lines.append(f"💸 Траты: {money(exp)}")
    lines.append(f"{'✅' if inc >= exp else '🔻'} Итог периода: {util.signed(inc - exp)}")
    days = (end - start).days + 1
    if exp and days > 1:
        lines.append(f"≈ {money0(exp / days)} в день")
    pp = calc.prev_period(period, start, end)
    if pp and exp:
        pe = calc.sum_tx(db, "expense", pp[0], pp[1])
        if pe > 0:
            lines.append(f"К прошлому периоду: {_pct(exp, pe)} ({money(pe)})")
    cats = calc.category_totals(db, "expense", start, end)
    if cats:
        lines.append("")
        month_view = period in ("month", "last_month")
        budgets = {r["name"]: r["budget"] for r in db.q("SELECT name,budget FROM categories")}
        for name, s, n in cats[:8]:
            frac = s / exp if exp else 0
            extra = ""
            if month_view and budgets.get(name):
                extra = f" · лимит {money(budgets[name])}"
            lines.append(f"{emojis.get(name, '🏷')} {esc(name)} — {money(s)} {bar(frac, 8)} {frac * 100:.0f}%{extra}")
        if len(cats) > 8:
            rest = sum(s for _, s, _ in cats[8:])
            lines.append(f"…остальное — {money(rest)}")
    big = db.one("SELECT * FROM tx WHERE deleted=0 AND kind='expense' AND date>=? AND date<=? "
                 "ORDER BY amount DESC LIMIT 1", (start.isoformat(), end.isoformat()))
    if big and exp and big["amount"] >= exp * 0.25 and days > 1:
        lines.append("")
        lines.append("Самая крупная: " + tx_line(big, emojis, ref))
    return "\n".join(lines)


# ---------- задачи ----------

def _task_line(t, ref: date) -> str:
    due = util.fmt_due(t["due"], ref) if t["due"] else ""
    tail = f" — {due}" if due else ""
    return f"▫️ {esc(t['text'])}{tail} <i>№{t['id']}</i>"


def tasks_view(db) -> str:
    ref = util.today()
    rows = [dict(r) for r in db.q("SELECT * FROM tasks WHERE deleted=0 AND done=0 ORDER BY "
                                  "CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, id")]
    done_today = db.one("SELECT COUNT(*) c FROM tasks WHERE deleted=0 AND done=1 AND done_at>=?",
                        (ref.isoformat(),))["c"]
    if not rows:
        extra = f"\nСегодня выполнено: {done_today}" if done_today else ""
        return "✅ Открытых задач нет." + extra
    groups = {"over": [], "today": [], "tom": [], "week": [], "later": [], "none": []}
    for t in rows:
        d = util.parse_date(t["due"])
        if d is None:
            groups["none"].append(t)
        elif d < ref:
            groups["over"].append(t)
        elif d == ref:
            groups["today"].append(t)
        elif d == ref + timedelta(days=1):
            groups["tom"].append(t)
        elif d <= ref + timedelta(days=7):
            groups["week"].append(t)
        else:
            groups["later"].append(t)
    titles = [("over", "🔴 Просрочено"), ("today", "🟠 Сегодня"), ("tom", "🟡 Завтра"),
              ("week", "🟢 На неделе"), ("later", "🔵 Позже"), ("none", "⚪️ Без даты")]
    out = ["📋 <b>Задачи</b>"]
    for key, title in titles:
        if groups[key]:
            out.append(f"\n{title}")
            out.extend(_task_line(t, ref) for t in groups[key])
    if done_today:
        out.append(f"\nСегодня выполнено: {done_today}")
    return "\n".join(out)


# ---------- долги ----------

def debts_view(db) -> str:
    rows = [dict(r) for r in db.q("SELECT * FROM debts WHERE deleted=0 AND closed=0 ORDER BY id")]
    if not rows:
        return "💳 Долгов нет."
    by: dict[tuple, dict] = {}
    for r in rows:
        key = (r["direction"], r["person"].casefold())
        e = by.setdefault(key, {"person": r["person"], "dir": r["direction"], "rem": 0.0, "notes": []})
        e["rem"] += r["amount"] - r["paid"]
        if r["note"]:
            e["notes"].append(r["note"])
    out = ["💳 <b>Долги</b>"]
    for title, d in (("Мне должны", "owed_to_me"), ("Я должен", "i_owe")):
        part = [e for e in by.values() if e["dir"] == d]
        if part:
            out.append(f"\n<b>{title}</b>")
            for e in part:
                note = f" ({esc(', '.join(e['notes'][:2]))})" if e["notes"] else ""
                out.append(f"👤 {esc(e['person'])} — {money(e['rem'])}{note}")
    lent, borrowed = calc.debts_open(db)
    out.append("")
    out.append(f"Итого: мне {money(lent)} · я {money(borrowed)}")
    return "\n".join(out)


# ---------- копилки ----------

def stashes_view(db, rates) -> str:
    rows = [dict(r) for r in db.q("SELECT * FROM stashes WHERE deleted=0 ORDER BY closed, id")]
    if not rows:
        return "🐷 Копилок пока нет. Скажи, например: «отложил 2000 в заначку» или «хочу накопить 60000 на ноутбук»."
    ref = util.today()
    out = ["🐷 <b>Заначки и цели</b>"]
    for s in rows:
        amt = calc.stash_amount(db, s["id"])
        cur = s["currency"]
        line = f"{'✅' if s['closed'] else '🐷'} <b>{esc(s['name'])}</b>: {money(amt, cur)}"
        if cur != util.base_cur():
            v = calc.stash_value_base(db, rates, s)
            if v is not None:
                line += f" (≈ {money(v)})"
        out.append(line)
        if s["target"]:
            frac = amt / s["target"] if s["target"] else 0
            out.append(f"   {bar(frac)} {frac * 100:.0f}% из {money(s['target'], cur)}")
            dl = util.parse_date(s["deadline"])
            left = s["target"] - amt
            if dl and left > 0 and not s["closed"]:
                months = max((dl.year - ref.year) * 12 + dl.month - ref.month, 1)
                out.append(f"   до {util.fmt_short(dl)}: откладывать ≈ {money0(left / months, cur)} в месяц")
    out.append("")
    out.append(f"Всего отложено: {money(calc.stash_total_value(db, rates))}")
    return "\n".join(out)


# ---------- подписки и сроки ----------

PERIOD_WORD = {"month": "каждый месяц", "year": "раз в год", "once": "разово"}


def sub_line(r, rates, ref: date) -> str:
    d = util.parse_date(r["next_due"])
    when = f"{util.fmt_short(d)} ({util.days_phrase((d - ref).days)})" if d else "—"
    if r["amount"]:
        base = calc.sub_base_amount(rates, r)
        amt = util.money_pair(r["amount"], r["currency"], base) if base is not None \
            else f"{money(r['amount'], r['currency'])} (курс неизвестен)"
        icon = "💼" if r["kind"] == "income" else "🔁"
        return f"{icon} <b>{esc(r['name'])}</b> — {amt} · {PERIOD_WORD.get(r['period'], '')} · {when}"
    return f"⏳ <b>{esc(r['name'])}</b> — срок до {when}"


def subs_view(db, rates) -> str:
    ref = util.today()
    rows = [dict(r) for r in db.q("SELECT * FROM recurring WHERE deleted=0 ORDER BY active DESC, next_due, id")]
    if not rows:
        return ("🔁 Регулярных платежей и сроков пока нет.\nНапример: «Claude Pro 20$ каждое 14-е», "
                "«сервер 300 до 3 ноября», «зарплата 50000 каждое 10-е», «домен до 15 марта».")
    out = ["🔁 <b>Платежи, подписки и сроки</b>"]
    active = [r for r in rows if r["active"]]
    paused = [r for r in rows if not r["active"]]
    for r in active:
        out.append(sub_line(r, rates, ref) + f" <i>№{r['id']}</i>")
    if paused:
        out.append("\n<b>На паузе</b>")
        out.extend(f"⏸ {esc(r['name'])} <i>№{r['id']}</i>" for r in paused)
    total, bad = calc.sub_monthly_total(db, rates)
    if total:
        out.append("")
        out.append(f"Расходы в месяц: ≈ {money0(total)} · в год: ≈ {money0(total * 12)}")
    if bad:
        out.append("⚠️ Для части платежей неизвестен курс — они не учтены в сумме.")
    return "\n".join(out)


# ---------- списки ----------

def recent_view(db, n: int = 10) -> str:
    rows = db.q("SELECT * FROM tx WHERE deleted=0 ORDER BY id DESC LIMIT ?", (n,))
    if not rows:
        return "Записей пока нет."
    em = db.emojis()
    ref = util.today()
    return "🧾 <b>Последние записи</b>\n" + "\n".join(tx_line(r, em, ref) for r in rows)


def journal_view(db, kind: str | None = None, period: str | None = None, text: str | None = None) -> str:
    ref = util.today()
    start, end, label = util.period_range(period or "last30", ref)
    sql = "SELECT * FROM journal WHERE deleted=0 AND date>=? AND date<=?"
    args = [start.isoformat(), end.isoformat()]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    rows = [dict(r) for r in db.q(sql + " ORDER BY date DESC, id DESC", args)]
    if text:
        rows = [r for r in rows if util.match_score(text, r["text"]) > 0]
    if not rows:
        return "📓 Записей в дневнике за этот период нет."
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    head = ", ".join(f"{KIND_ICON.get(k, '📓')} {k}: {v}" for k, v in counts.items())
    out = [f"📓 <b>Дневник</b> — {esc(label.lower())}", head, ""]
    for r in rows[:15]:
        d = util.parse_date(r["date"]) or ref
        val = f" ({num(r['value'])})" if r["value"] is not None else ""
        out.append(f"{KIND_ICON.get(r['kind'], '📓')} {util.fmt_date(d, ref)}: {esc(r['text'])}{val}")
    if len(rows) > 15:
        out.append(f"…и ещё {len(rows) - 15}")
    return "\n".join(out)


def search_view(db, rates, text: str) -> str:
    ref = util.today()
    em = db.emojis()
    hits: list[tuple[str, str]] = []  # (дата, строка)
    for r in db.q("SELECT * FROM tx WHERE deleted=0 ORDER BY date DESC, id DESC LIMIT 4000"):
        if util.match_score(text, f"{r['description']} {r['category']}") > 0:
            hits.append((r["date"], "💸 " + tx_line(r, em, ref)))
    for r in db.q("SELECT * FROM tasks WHERE deleted=0 ORDER BY id DESC LIMIT 1000"):
        if util.match_score(text, r["text"]) > 0:
            d = (r["due"] or r["created_at"])[:10]
            hits.append((d, f"{'✅' if r['done'] else '▫️'} задача: {esc(r['text'])} ({util.fmt_due(r['due'], ref) or 'без даты'})"))
    for r in db.q("SELECT * FROM journal WHERE deleted=0 ORDER BY date DESC, id DESC LIMIT 2000"):
        if util.match_score(text, r["text"]) > 0:
            hits.append((r["date"], f"{KIND_ICON.get(r['kind'], '📓')} {util.fmt_date(util.parse_date(r['date']) or ref, ref)}: {esc(r['text'])}"))
    for r in db.q("SELECT * FROM debts WHERE deleted=0"):
        if util.match_score(text, f"{r['person']} {r['note']}") > 0:
            rem = r["amount"] - r["paid"]
            hits.append((r["created_at"][:10], f"💳 {esc(r['person'])}: {money(r['amount'])}, осталось {money(rem)}"))
    for r in db.q("SELECT * FROM recurring WHERE deleted=0"):
        if util.match_score(text, r["name"]) > 0:
            hits.append((r["next_due"][:10], sub_line(dict(r), rates, ref)))
    if not hits:
        return f"🔍 По запросу «{esc(text)}» ничего не нашёл."
    hits.sort(key=lambda h: h[0], reverse=True)
    out = [f"🔍 <b>«{esc(text)}»</b> — нашёл {len(hits)}"]
    out.extend(h[1] for h in hits[:12])
    if len(hits) > 12:
        out.append(f"…и ещё {len(hits) - 12}")
    return "\n".join(out)


def history_view(db, limit: int = 15) -> str:
    rows = db.q("SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,))
    if not rows:
        return "Изменений пока не было."
    out = ["🕘 <b>Последние действия</b> (можно написать «отмени»)"]
    for r in rows:
        mark = "↩️" if r["undone"] else "•"
        out.append(f"{mark} {esc(r['ts'][5:16])} — {esc(r['summary'])}")
    return "\n".join(out)


def categories_view(db) -> str:
    ref = util.today()
    out = ["📂 <b>Категории</b>"]
    for r in db.q("SELECT * FROM categories ORDER BY rowid"):
        spent = calc.month_category_sum(db, r["name"], ref)
        extra = ""
        if spent:
            extra = f" — {money(spent)} в этом месяце"
        if r["budget"]:
            extra += f" · лимит {money(r['budget'])}"
        out.append(f"{r['emoji'] or '🏷'} {esc(r['name'])}{extra}")
    mb = db.setting("month_budget")
    if mb:
        out.append(f"\nОбщий лимит на месяц: {money(float(mb))}")
    return "\n".join(out)


def rates_view(db, rates) -> str:
    codes = {"USD", "EUR"}
    for tbl in ("tx", "recurring", "stashes"):
        for r in db.q(f"SELECT DISTINCT currency c FROM {tbl}"):
            codes.add(r["c"])
    codes.discard(util.base_cur())
    out = ["💱 <b>Курсы</b>"]
    for c in sorted(codes):
        v = rates.get(c)
        if v is None:
            out.append(f"{c}: неизвестен — напиши «курс {c} 95»")
        else:
            out.append(f"{c}: {num(v)} {util.SYMBOLS.get(util.base_cur(), util.base_cur())}" +
                       (" (вручную)" if rates.is_manual(c) else " (ЦБ)"))
    ok = db.setting("rates_ok")
    out.append(f"\nПоследнее обновление: {ok or 'ещё не было'}")
    out.append("Чтобы вернуть авто-курс: «курс доллара авто».")
    return "\n".join(out)


def settings_view(db) -> str:
    on = lambda k, d="on": "включена" if db.setting(k, d) == "on" else "выключена"
    today = util.today().isoformat()
    tokens = db.setting(f"ai_tokens_{today}", "0")
    reqs = db.setting(f"ai_requests_{today}", "0")
    return "\n".join([
        "⚙️ <b>Настройки</b>",
        f"☀️ Утренняя сводка ({config.MORNING_TIME}): {on('morning')} — «утро выкл / утро вкл»",
        f"📊 Итоги недели (вс {config.WEEKLY_TIME}): {on('weekly')} — «итоги недели выкл / вкл»",
        f"💱 Базовая валюта: {util.base_cur()}",
        f"🕒 Часовой пояс: {config.TZ}",
        f"🤖 ИИ сегодня: {reqs} запросов, ≈{tokens} токенов (модели: {', '.join(config.LLM_MODELS)})",
        "💾 Бэкап базы: по воскресеньям или командой «бэкап»",
    ])


def help_text() -> str:
    return "\n".join([
        "🤖 <b>Я твой личный помощник.</b> Пиши как человеку или надиктуй голосом.",
        "",
        "<b>Деньги</b>",
        "• «300 шаурма», «потратил 15 евро на игру», «пришла зарплата 50к»",
        "• «сейчас у меня 12000» — стартовый баланс",
        "• «баланс», «статистика», «статистика за неделю», «сколько потратил на еду»",
        "• «лимит на еду 8000» — предупрежу на 80% и 100%",
        "",
        "<b>Дела</b>",
        "• «напомни завтра в 10 сдать отчёт», «задачи», «сделал отчёт»",
        "",
        "<b>Долги и заначки</b>",
        "• «одолжил Васе 500», «Вася вернул 300», «долги»",
        "• «отложил 2000 в заначку», «хочу накопить 60000 на ноутбук до марта»",
        "",
        "<b>Подписки и сроки</b>",
        "• «Claude Pro 20$ каждое 14-е», «сервер 300 до 3 ноября», «домен до 15 марта», «подписки»",
        "• За 3 и 1 день напомню, в день платежа спрошу «оплатил?»",
        "",
        "<b>Дневник</b>",
        "• «жим 60 на 3 по 8», «спал 6 часов», «вес 72»; потом «когда я последний раз был у стоматолога»",
        "",
        "<b>Если ошибся</b>",
        "• «отмени», «это не еда, а транспорт», «было 400», кнопки ✏️ и 🗑 под записями",
        "• «последние» — список с номерами, «что я менял», «верни #12»",
        "",
        "Курсы валют: «курсы», «курс доллара 95», «курс доллара авто». Настройки: «настройки». Бэкап: «бэкап».",
    ])


# ---------- сводки ----------

def morning_brief(db, rates, ref: date | None = None) -> str:
    ref = ref or util.today()
    lines = [f"☀️ <b>Доброе утро!</b> {util.WD_FULL[ref.weekday()].capitalize()}, {util.fmt_short(ref)}"]
    tasks = [dict(r) for r in db.q("SELECT * FROM tasks WHERE deleted=0 AND done=0 AND due IS NOT NULL ORDER BY due")]
    over = [t for t in tasks if (util.parse_date(t["due"]) or ref) < ref]
    today = [t for t in tasks if util.parse_date(t["due"]) == ref]
    if over:
        lines.append("\n🔴 <b>Просрочено</b>")
        lines.extend(_task_line(t, ref) for t in over[:6])
    if today:
        lines.append("\n🟠 <b>Сегодня</b>")
        lines.extend(_task_line(t, ref) for t in today[:8])
    # платежи и сроки сюда не дублируем: о них напоминает отдельное сообщение с кнопками
    a = calc.allowance(db, rates, ref)
    if a:
        pd = a["per_day"]
        lines.append("")
        if pd >= 0:
            lines.append(f"💰 До «{esc(a['next_name'])}» {util.days_phrase(a['days'])}: можно тратить ≈ {money0(pd)} в день")
        else:
            lines.append(f"🔴 До «{esc(a['next_name'])}» {util.days_phrase(a['days'])}: денег на всё не хватает")
    y = ref - timedelta(days=1)
    ys = calc.sum_tx(db, "expense", y, y)
    if ys:
        lines.append(f"Вчера потрачено: {money(ys)}")
    if len(lines) == 1:
        lines.append("Планов и платежей на сегодня нет. Хорошего дня!")
    return "\n".join(lines)


def week_insight(db, start: date, end: date) -> str | None:
    cur = {c: s for c, s, _ in calc.category_totals(db, "expense", start, end)}
    ps = start - timedelta(days=7)
    prev = {c: s for c, s, _ in calc.category_totals(db, "expense", ps, ps + (end - start))}
    best = None
    for c, s in cur.items():
        p = prev.get(c, 0)
        if s - p >= 500 and (p == 0 and s >= 1000 or p > 0 and s / p >= 1.3):
            if best is None or s - p > best[1]:
                best = (c, s - p, p)
    if best:
        c, inc, p = best
        if p:
            return f"На «{c}» ушло на {money(inc)} больше, чем неделей раньше."
        return f"Новая крупная статья: «{c}» — {money(cur[c])}."
    days = db.one("SELECT COUNT(DISTINCT date) d FROM tx WHERE deleted=0 AND kind='expense' "
                  "AND date>=? AND date<=?", (start.isoformat(), end.isoformat()))["d"]
    span = (end - start).days + 1
    if span >= 5 and 0 < days < 3:
        return f"Траты записаны только в {days} из {span} дней — возможно, часть не внесена."
    return None


def weekly_review(db, rates, ref: date | None = None) -> str:
    ref = ref or util.today()
    start = ref - timedelta(days=ref.weekday())
    end = ref
    em = db.emojis()
    exp = calc.sum_tx(db, "expense", start, end)
    inc = calc.sum_tx(db, "income", start, end)
    ps = start - timedelta(days=7)
    pe = calc.sum_tx(db, "expense", ps, ps + (end - start))
    lines = [f"📊 <b>Итоги недели</b> ({util.fmt_short(start)} – {util.fmt_short(end)})"]
    cmp_ = f" ({_pct(exp, pe)} к прошлой)" if pe > 0 and exp else ""
    lines.append(f"💸 Потрачено: {money(exp)}{cmp_}")
    cats = calc.category_totals(db, "expense", start, end)[:3]
    if cats:
        lines.append("   Топ: " + " · ".join(f"{em.get(c, '🏷')} {esc(c)} {money(s)}" for c, s, _ in cats))
    if inc:
        lines.append(f"💰 Доходы: {money(inc)}")
    closed = db.one("SELECT COUNT(*) c FROM tasks WHERE deleted=0 AND done=1 AND done_at>=?",
                    (start.isoformat(),))["c"]
    open_n = db.one("SELECT COUNT(*) c FROM tasks WHERE deleted=0 AND done=0")["c"]
    overdue = db.one("SELECT COUNT(*) c FROM tasks WHERE deleted=0 AND done=0 AND due IS NOT NULL AND due<?",
                     (ref.isoformat(),))["c"]
    lines.append(f"✅ Задач закрыто: {closed} · открыто: {open_n}" + (f" (просрочено {overdue})" if overdue else ""))
    wk = db.one("SELECT COUNT(*) c FROM journal WHERE deleted=0 AND kind='workout' AND date>=? AND date<=?",
                (start.isoformat(), end.isoformat()))["c"]
    if wk:
        lines.append(f"🏋️ Тренировок: {wk}")
    owed = [dict(r) for r in db.q("SELECT * FROM debts WHERE deleted=0 AND closed=0 AND direction='owed_to_me'")]
    if owed:
        lines.append("💳 Должны тебе: " + ", ".join(f"{esc(d['person'])} {money(d['amount'] - d['paid'])}" for d in owed[:4]))
    nxt = []
    for r in db.q("SELECT * FROM recurring WHERE active=1 AND deleted=0 ORDER BY next_due"):
        d = util.parse_date(r["next_due"])
        if d and ref < d <= ref + timedelta(days=7):
            nxt.append(f"{esc(r['name'])} ({util.fmt_short(d)})")
    if nxt:
        lines.append("🔁 На следующей неделе: " + ", ".join(nxt[:5]))
    ins = week_insight(db, start, end)
    if ins:
        lines.append("\n💡 " + esc(ins))
    return "\n".join(lines)
