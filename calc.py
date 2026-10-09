"""Все вычисления по деньгам — только здесь, обычным кодом. ИИ цифры не считает."""
from __future__ import annotations

from datetime import date, timedelta

import util
from rates import NoRate


# ---------- траты и доходы ----------

def sum_tx(db, kind: str, start: date | None = None, end: date | None = None,
           category: str | None = None) -> float:
    sql = "SELECT COALESCE(SUM(amount),0) s FROM tx WHERE deleted=0 AND kind=?"
    args: list = [kind]
    if start:
        sql += " AND date>=?"
        args.append(start.isoformat())
    if end:
        sql += " AND date<=?"
        args.append(end.isoformat())
    if category:
        sql += " AND category=?"
        args.append(category)
    return float(db.one(sql, args)["s"])


def category_totals(db, kind: str, start: date, end: date) -> list[tuple[str, float, int]]:
    rows = db.q("SELECT category, SUM(amount) s, COUNT(*) n FROM tx WHERE deleted=0 AND kind=? "
                "AND date>=? AND date<=? GROUP BY category ORDER BY s DESC",
                (kind, start.isoformat(), end.isoformat()))
    return [(r["category"], float(r["s"]), int(r["n"])) for r in rows]


def month_bounds(d: date) -> tuple[date, date]:
    return date(d.year, d.month, 1), date(d.year, d.month, util.last_day(d.year, d.month))


def month_category_sum(db, category: str, d: date) -> float:
    s, e = month_bounds(d)
    return sum_tx(db, "expense", s, e, category)


def month_expense_sum(db, d: date) -> float:
    s, e = month_bounds(d)
    return sum_tx(db, "expense", s, e)


# ---------- копилки ----------

def stash_amount(db, sid: int) -> float:
    """Сколько в копилке — в её собственной валюте."""
    return float(db.one("SELECT COALESCE(SUM(delta),0) s FROM stash_moves WHERE stash_id=?", (sid,))["s"])


def stash_base_cash(db) -> float:
    """Сколько свободных денег ушло в копилки (по курсу на момент пополнения)."""
    return float(db.one("SELECT COALESCE(SUM(m.base_delta),0) s FROM stash_moves m "
                        "JOIN stashes s ON s.id=m.stash_id WHERE s.deleted=0")["s"])


def stash_value_base(db, rates, s: dict) -> float | None:
    amt = stash_amount(db, s["id"])
    try:
        return rates.to_base(amt, s["currency"])[0]
    except NoRate:
        return None


def stash_total_value(db, rates) -> float:
    total = 0.0
    for s in db.q("SELECT * FROM stashes WHERE deleted=0"):
        v = stash_value_base(db, rates, dict(s))
        if v is not None:
            total += v
    return total


# ---------- долги ----------

def debts_cash(db) -> float:
    """Влияние долгов на свободные деньги. Простить долг = деньги не вернутся (потеря остаётся)."""
    total = 0.0
    for r in db.q("SELECT direction, amount, paid FROM debts WHERE deleted=0"):
        rem = r["amount"] - r["paid"]
        total += -rem if r["direction"] == "owed_to_me" else rem
    return total


def debts_open(db) -> tuple[float, float]:
    """(мне должны, я должен) — по незакрытым долгам."""
    lent = borrowed = 0.0
    for r in db.q("SELECT direction, amount, paid FROM debts WHERE deleted=0 AND closed=0"):
        rem = max(r["amount"] - r["paid"], 0.0)
        if r["direction"] == "owed_to_me":
            lent += rem
        else:
            borrowed += rem
    return lent, borrowed


def balance(db) -> float:
    """Свободные деньги = старт + доходы − траты − отложенное + (долги: взял − дал)."""
    opening = float(db.setting("opening", "0") or 0)
    return (opening + sum_tx(db, "income") - sum_tx(db, "expense")
            - stash_base_cash(db) + debts_cash(db))


# ---------- даты платежей ----------

def advance_due(due: date, period: str, anchor: int | None) -> date:
    if period == "month":
        y, m = util.add_months(due, 1)
        return util.occurrence(anchor or due.day, y, m)
    if period == "year":
        return util.occurrence(anchor or due.day, due.year + 1, due.month)
    return due


def next_occurrence(day: int, ref: date) -> date:
    """Ближайшее «N-е число», включая сегодняшний день."""
    for k in (0, 1, 2):
        y, m = util.add_months(ref, k)
        d = util.occurrence(day, y, m)
        if d >= ref:
            return d
    return util.occurrence(day, *util.add_months(ref, 3))


def sub_base_amount(rates, row) -> float | None:
    if not row["amount"]:
        return 0.0
    try:
        return rates.to_base(row["amount"], row["currency"])[0]
    except NoRate:
        return None


def sub_monthly_total(db, rates) -> tuple[float, int]:
    """(сумма расходов в месяц, сколько записей не удалось пересчитать)."""
    total, bad = 0.0, 0
    for r in db.q("SELECT * FROM recurring WHERE active=1 AND deleted=0 AND kind='expense' "
                  "AND amount>0 AND period!='once'"):
        b = sub_base_amount(rates, r)
        if b is None:
            bad += 1
            continue
        total += b / 12 if r["period"] == "year" else b
    return round(total, 2), bad


def next_income(db, ref: date) -> tuple[date, dict] | None:
    best = None
    for r in db.q("SELECT * FROM recurring WHERE active=1 AND deleted=0 AND kind='income'"):
        d = util.parse_date(r["next_due"])
        if d and d > ref and (best is None or d < best[0]):
            best = (d, dict(r))
    return best


def allowance(db, rates, ref: date | None = None) -> dict | None:
    """Сколько можно тратить в день до ближайшего дохода из регулярных (зарплата, аванс)."""
    ref = ref or util.today()
    nxt = next_income(db, ref)
    if not nxt:
        return None
    nxt_date, nxt_row = nxt
    days = (nxt_date - ref).days
    items = []
    upcoming = 0.0
    for r in db.q("SELECT * FROM recurring WHERE active=1 AND deleted=0 AND kind='expense' AND amount>0"):
        d = util.parse_date(r["next_due"])
        if d and ref <= d < nxt_date:
            b = sub_base_amount(rates, r)
            if b is not None:
                upcoming += b
                items.append((r["name"], b, d))
    bal = balance(db)
    return {"balance": bal, "upcoming": upcoming, "items": items, "next": nxt_date,
            "next_name": nxt_row["name"], "days": days,
            "per_day": (bal - upcoming) / max(days, 1)}


def prev_period(name: str, start: date, end: date) -> tuple[date, date] | None:
    """Сопоставимый прошлый период для сравнения «к прошлому ...»."""
    if name == "month":
        y, m = util.add_months(start, -1)
        ps = date(y, m, 1)
        pe = min(ps + (end - start), date(y, m, util.last_day(y, m)))
        return ps, pe
    if name == "last_month":
        y, m = util.add_months(start, -1)
        return date(y, m, 1), date(y, m, util.last_day(y, m))
    if name in ("week", "last_week", "last7"):
        ps = start - timedelta(days=7)
        return ps, ps + (end - start)
    if name == "last30":
        ps = start - timedelta(days=30)
        return ps, ps + (end - start)
    return None
