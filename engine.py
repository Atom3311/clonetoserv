"""Движок: выполняет «действия» (их присылает ИИ или быстрый разбор) и отвечает текстом + кнопками.

Все цифры считает код, не ИИ. Любое изменение пишется в журнал `changes`, поэтому
«отмени», «верни #12» и «что я менял» работают для всего.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import timedelta

import calc
import config
import util
import views
from db import TABLES, SOFT
from rates import NoRate
from util import esc, money, num


@dataclass
class Reply:
    text: str = ""
    kb: list = field(default_factory=list)   # [[(подпись, callback_data), ...], ...]
    kind: str = ""
    clear: bool = False                       # убрать кнопки у исходного сообщения
    count: int = 1                            # сколько записей слито в одно сообщение


class EngineError(Exception):
    pass


ENT = {"tx": "tx", "task": "tasks", "debt": "debts", "stash": "stashes", "sub": "recurring", "journal": "journal"}
ENT_ALIAS = {
    "transaction": "tx", "expense": "tx", "income": "tx", "trata": "tx", "tasks": "task", "reminder": "task",
    "todo": "task", "debts": "debt", "stashes": "stash", "goal": "stash", "savings": "stash",
    "subscription": "sub", "payment": "sub", "recurring": "sub", "subs": "sub", "note": "journal",
}
TYPE_ALIAS = {"transaction": "expense", "spend": "expense", "spent": "expense", "reminder": "task",
              "todo": "task", "stash_create": "stash", "goal": "stash", "subscription": "sub",
              "recurring": "sub", "balance": "set_balance", "edit": "edit_tx", "chat": "say", "reply": "say"}
PERIOD_ALIAS = {"this_week": "week", "this_month": "month", "last_7_days": "last7", "last_30_days": "last30",
                "previous_month": "last_month", "prev_month": "last_month", "previous_week": "last_week",
                "prev_week": "last_week", "this_year": "year", "all_time": "all", "7days": "last7", "30days": "last30"}
JOURNAL_KINDS = ("workout", "food", "sleep", "weight", "mood", "note")
FIELD_PROMPTS = {"amount": "💲 Напиши новую сумму (например 450 или 20$)",
                 "date": "📅 Напиши дату: вчера, 05.10 или 2026-10-05",
                 "description": "📝 Напиши новое описание",
                 "newcat": "📂 Напиши название новой категории"}


def _f(x, default=None):
    if x is None or x == "":
        return default
    if isinstance(x, bool):
        return default
    if isinstance(x, (int, float)):
        return float(x)
    m = util.parse_money(str(x))
    return m[0] if m else default


def _id(x):
    if x is None or x == "":
        return None
    if isinstance(x, str):
        s = x.strip().lstrip("#№")
        if s.casefold() in ("last", "latest", "последняя", "последнее", "последний"):
            return "last"
        return int(s) if s.isdigit() else None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _txt(x, n=100) -> str:
    return " ".join(str(x or "").split())[:n]


def _bool(x) -> bool | None:
    if isinstance(x, bool):
        return x
    if x is None:
        return None
    s = str(x).strip().casefold()
    if s in ("1", "true", "yes", "on", "да", "вкл"):
        return True
    if s in ("0", "false", "no", "off", "нет", "выкл"):
        return False
    return None


class Engine:
    def __init__(self, db, rates):
        self.db = db
        self.rates = rates
        self.last: dict[str, int] = {}
        self.pending: dict | None = None

    # ------------------------------------------------------------------ запуск
    def run(self, actions, raw: str = "") -> list[Reply]:
        out: list[Reply] = []
        for a in actions or []:
            if not isinstance(a, dict):
                continue
            t = re.sub(r"[^a-z_]", "", str(a.get("type", "")).strip().lower())
            t = TYPE_ALIAS.get(t, t)
            fn = getattr(self, "a_" + t, None) if t else None
            if fn is None:
                continue
            try:
                r = fn(a, raw)
            except EngineError as e:
                r = Reply("⚠️ " + esc(str(e)))
            except NoRate as e:
                r = Reply(self._norate(e.code))
            if r is None:
                continue
            out.extend(r if isinstance(r, list) else [r])
        return self._merge(out)

    @staticmethod
    def _merge(replies: list[Reply]) -> list[Reply]:
        merged: list[Reply] = []
        for r in replies:
            if merged and r.kind == "tx_added" and merged[-1].kind == "tx_added":
                merged[-1].text += "\n" + r.text
                merged[-1].kb.extend(r.kb)
                merged[-1].count += 1
            else:
                merged.append(r)
        for r in merged:   # в сборном сообщении подписываем кнопки номерами записей
            if r.count > 1:
                for row in r.kb:
                    for i, (label, data) in enumerate(row):
                        parts = data.split(":")
                        if parts[0] == "tx" and parts[1] in ("e", "d"):
                            row[i] = (("✏️ #" if parts[1] == "e" else "🗑 #") + parts[2], data)
        return merged

    @staticmethod
    def _norate(code: str) -> str:
        return (f"⚠️ Не знаю курс {esc(code)}. Напиши «курс {esc(code)} 95» (сколько {util.base_cur()} за 1 {esc(code)}) "
                f"и повтори запись. Обычно курсы подтягиваются сами с сайта ЦБ.")

    # ------------------------------------------------------------------ поиск записей
    def _plain(self, entity: str, row: dict) -> str:
        if entity == "tx":
            d = f" {row['description']}" if row.get("description") else ""
            return f"#{row['id']} {row['category']} {num(row['orig_amount'])} {row['currency']}{d}"
        if entity == "task":
            return f"задача «{row['text']}»"
        if entity == "debt":
            return f"долг: {row['person']} {num(row['amount'])}"
        if entity == "stash":
            return f"копилка «{row['name']}»"
        if entity == "sub":
            return f"платёж «{row['name']}»"
        return f"запись «{row.get('text', '')}»"

    def _keytext(self, entity: str, row) -> str:
        if entity == "tx":
            return f"{row['description']} {row['category']}"
        return str(row[{"task": "text", "debt": "person", "stash": "name", "sub": "name", "journal": "text"}[entity]])

    def _resolve(self, entity: str, a: dict, only_open: bool = False):
        table = ENT[entity]
        rid = _id(a.get("id"))
        query = ""
        for k in ("name", "text", "person", "query", "description", "target_text"):
            if a.get(k):
                query = str(a[k])
                break
        extra = " AND done=0" if (only_open and entity == "task") else ""
        if rid == "last" or (rid is None and not query):
            lid = self.last.get(entity)
            row = self.db.live(table, lid) if lid else None
            if row and only_open and entity == "task" and row["done"]:
                row = None
            if not row:
                r = self.db.one(f"SELECT * FROM {table} WHERE deleted=0{extra} ORDER BY id DESC LIMIT 1")
                row = dict(r) if r else None
            if not row:
                raise EngineError("Не нашёл, что менять: записей нет.")
            return row
        if isinstance(rid, int):
            row = self.db.live(table, rid)
            if row and not (only_open and entity == "task" and row["done"]):
                return row
        if query:
            rows = self.db.q(f"SELECT * FROM {table} WHERE deleted=0{extra} ORDER BY id DESC LIMIT 500")
            best, score = None, 0
            for r in rows:
                s = util.match_score(query, self._keytext(entity, r))
                if s > score:
                    best, score = r, s
            if best is not None:
                return dict(best)
        raise EngineError("Не нашёл такую запись. Посмотри список командой («последние», «задачи», «подписки») и укажи номер.")

    # ------------------------------------------------------------------ траты и доходы
    def _budget_lines(self, cname: str, d, added_base: float) -> list[str]:
        out = []
        spent = calc.month_category_sum(self.db, cname, d)
        line = f"{esc(cname)} в {util.MONTHS_PREP[d.month - 1]}: {money(spent)}"
        cat = self.db.find_category(cname)
        if cat and cat["budget"]:
            b = cat["budget"]
            before = spent - added_base
            if spent >= b > before:
                out.append(f"🔴 Лимит по «{esc(cname)}» превышен: {money(spent)} из {money(b)}")
                line = ""
            elif spent >= 0.8 * b > before:
                out.append(f"🟠 По «{esc(cname)}» потрачено уже 80% лимита: {money(spent)} из {money(b)}")
                line = ""
            else:
                line += f" (лимит {money(b)}, {spent / b * 100:.0f}%)"
        if line:
            out.insert(0, line)
        mb = self.db.setting("month_budget")
        if mb:
            total = calc.month_expense_sum(self.db, d)
            mb = float(mb)
            before = total - added_base
            if total >= mb > before:
                out.append(f"🔴 Общий лимит месяца превышен: {money(total)} из {money(mb)}")
            elif total >= 0.8 * mb > before:
                out.append(f"🟠 Общий лимит месяца: уже 80% ({money(total)} из {money(mb)})")
        return out

    def _add_tx(self, kind: str, a: dict, raw: str) -> Reply:
        amount = _f(a.get("amount"))
        if not amount or amount <= 0:
            raise EngineError("Не понял сумму. Напиши, например: «300 шаурма».")
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        base, _ = self.rates.to_base(amount, cur)
        cname, created = self.db.ensure_category(a.get("category") or ("Зарплата" if kind == "income" else "Другое"),
                                                 a.get("emoji"))
        d = util.parse_human_date(a.get("date")) or util.today()
        rid = self.db.insert("tx", kind=kind, amount=base, currency=cur, orig_amount=amount, category=cname,
                             description=_txt(a.get("description")), date=d.isoformat(),
                             raw=str(raw)[:500], created_at=util.stamp())
        row = self.db.get("tx", rid)
        self.db.log_change("tx", rid, "create", "добавлена " + self._plain("tx", row), after=row)
        self.last["tx"] = rid
        em = self.db.emojis()
        head = ("✅ " if kind == "expense" else "💰 ") + views.tx_line(row, em, util.today())
        lines = [head]
        if created:
            lines.append(f"   🆕 Новая категория: {esc(cname)}")
        if kind == "expense":
            lines.extend("   " + x for x in self._budget_lines(cname, d, base))
        else:
            lines.append(f"   Свободно: {money(calc.balance(self.db))}")
        return Reply("\n".join(lines), [[("✏️ Изменить", f"tx:e:{rid}"), ("🗑 Удалить", f"tx:d:{rid}")]], kind="tx_added")

    def a_expense(self, a, raw):
        return self._add_tx("expense", a, raw)

    def a_income(self, a, raw):
        return self._add_tx("income", a, raw)

    def a_edit_tx(self, a, raw):
        row = self._resolve("tx", a)
        fields = {k: a.get(k) for k in ("amount", "currency", "category", "description", "date", "kind")
                  if a.get(k) not in (None, "")}
        return self.apply_tx_edit(row["id"], fields)

    def apply_tx_edit(self, rid: int, fields: dict) -> Reply:
        before = self.db.live("tx", rid)
        if not before:
            raise EngineError("Эта запись уже удалена.")
        upd, notes = {}, []
        amount = _f(fields.get("amount"))
        cur = util.norm_currency(fields.get("currency"))
        if amount is not None or cur:
            amount = amount or before["orig_amount"]
            cur = cur or before["currency"]
            base, _ = self.rates.to_base(amount, cur)
            upd.update(amount=base, currency=cur, orig_amount=amount)
        if fields.get("category"):
            cname, created = self.db.ensure_category(fields["category"], fields.get("emoji"))
            upd["category"] = cname
            if created:
                notes.append(f"🆕 Новая категория: {esc(cname)}")
        if "description" in fields and fields["description"] is not None:
            upd["description"] = _txt(fields["description"])
        if fields.get("date"):
            d = util.parse_human_date(str(fields["date"]))
            if not d:
                raise EngineError("Не понял дату. Напиши: вчера, 05.10 или 2026-10-05.")
            upd["date"] = d.isoformat()
        if fields.get("kind") in ("expense", "income"):
            upd["kind"] = fields["kind"]
        if not upd:
            raise EngineError("Не понял, что именно изменить.")
        self.db.update("tx", rid, **upd)
        after = self.db.get("tx", rid)
        self.db.log_change("tx", rid, "update", "изменена " + self._plain("tx", after), before=before, after=after)
        self.last["tx"] = rid
        text = "✏️ Исправил:\n" + views.tx_line(after, self.db.emojis(), util.today())
        if notes:
            text += "\n" + "\n".join(notes)
        return Reply(text, [[("✏️ Изменить", f"tx:e:{rid}"), ("🗑 Удалить", f"tx:d:{rid}")]])

    # ------------------------------------------------------------------ удаление, возврат, отмена
    def a_delete(self, a, raw):
        ent = str(a.get("entity") or "tx").strip().casefold()
        ent = ENT_ALIAS.get(ent, ent)
        if ent == "category":
            return self._delete_category(a)
        if ent not in ENT:
            raise EngineError("Не понял, что удалить.")
        row = self._resolve(ent, a)
        return self.delete_row(ent, row["id"])

    def delete_row(self, ent: str, rid: int) -> Reply:
        table = ENT[ent]
        before = self.db.live(table, rid)
        if not before:
            return Reply("Уже удалено.", clear=True)
        self.db.update(table, rid, deleted=1)
        self.db.log_change(table, rid, "delete", "удалено: " + self._plain(ent, before), before=before)
        extra = ""
        if ent == "stash":
            extra = "\nДеньги из этой копилки снова считаются свободными."
        return Reply(f"🗑 Удалил: {esc(self._plain(ent, before))}{extra}",
                     [[("↩️ Вернуть", f"r:{ent}:{rid}")]], clear=True)

    def _delete_category(self, a) -> Reply:
        name = a.get("name") or a.get("category")
        cat = self.db.find_category(name or "")
        if not cat:
            raise EngineError("Не нашёл такую категорию.")
        if cat["name"] == "Другое":
            raise EngineError("Категорию «Другое» удалить нельзя.")
        self.db.ensure_category("Другое")
        n = self._move_category(cat["name"], "Другое")
        self.db.exec("DELETE FROM categories WHERE name=?", (cat["name"],))
        return Reply(f"🗑 Категория «{esc(cat['name'])}» удалена, записи ({n}) перенесены в «Другое». Это нельзя отменить командой.")

    def a_restore(self, a, raw):
        ent = str(a.get("entity") or "tx").strip().casefold()
        ent = ENT_ALIAS.get(ent, ent)
        if ent not in ENT:
            raise EngineError("Не понял, что вернуть.")
        table = ENT[ent]
        rid = _id(a.get("id"))
        if isinstance(rid, int):
            row = self.db.get(table, rid)
        else:
            r = self.db.one(f"SELECT * FROM {table} WHERE deleted=1 ORDER BY id DESC LIMIT 1")
            row = dict(r) if r else None
        if not row or not row.get("deleted"):
            raise EngineError("Нечего возвращать: такой удалённой записи нет.")
        return self.restore_row(ent, row["id"])

    def restore_row(self, ent: str, rid: int) -> Reply:
        table = ENT[ent]
        row = self.db.get(table, rid)
        if not row:
            return Reply("Не нашёл запись.", clear=True)
        if not row.get("deleted"):
            return Reply("Запись и так на месте.", clear=True)
        self.db.update(table, rid, deleted=0)
        self.db.log_change(table, rid, "restore", "возвращено: " + self._plain(ent, row), before=row)
        self.last[ent] = rid
        return Reply(f"↩️ Вернул: {esc(self._plain(ent, row))}", clear=True)

    def a_undo(self, a=None, raw=""):
        ch = self.db.one("SELECT * FROM changes WHERE undone=0 ORDER BY id DESC LIMIT 1")
        if not ch:
            return Reply("Отменять нечего.")
        self._invert(dict(ch))
        self.db.exec("UPDATE changes SET undone=1 WHERE id=?", (ch["id"],))
        return Reply(f"↩️ Отменил: {esc(ch['summary'])}")

    def _invert(self, ch: dict) -> None:
        table, rid, act = ch["entity"], ch["entity_id"], ch["action"]
        before = json.loads(ch["before"]) if ch["before"] else None
        after = json.loads(ch["after"]) if ch["after"] else None
        if table == "paid":   # оплата регулярного платежа: запись + сдвиг даты
            if after and after.get("tx_id"):
                self.db.update("tx", after["tx_id"], deleted=1)
            if before:
                before.pop("id", None)
                self.db.update("recurring", rid, **before)
            return
        assert table in TABLES
        if act == "create":
            if table in SOFT:
                self.db.update(table, rid, deleted=1)
            else:
                self.db.exec(f"DELETE FROM {table} WHERE id=?", (rid,))
        elif act == "update" and before:
            before.pop("id", None)
            self.db.update(table, rid, **before)
        elif act == "delete":
            self.db.update(table, rid, deleted=0)
        elif act == "restore":
            self.db.update(table, rid, deleted=1)

    # ------------------------------------------------------------------ задачи
    def _calc_remind(self, due, remind_str):
        explicit = bool(remind_str) or (util.parse_dt(due) is not None and util.parse_dt(due)[1] is not None)
        r = util.remind_from(remind_str) if remind_str else None
        if not r and due:
            r = util.remind_from(due)
        if not r:
            return None, 0, ""
        dt = util.to_datetime(r)
        if dt and dt <= util.now():
            return r, 1, ("\nВремя уже прошло — напоминать не буду." if explicit else "")
        return r, 0, ""

    def a_task(self, a, raw):
        text = _txt(a.get("text"), 200)
        if not text:
            raise EngineError("Не понял, что за задача.")
        due = util.norm_due(a.get("due"))
        remind, reminded, note = self._calc_remind(due, a.get("remind"))
        rid = self.db.insert("tasks", text=text, due=due, remind_at=remind, reminded=reminded, created_at=util.stamp())
        row = self.db.get("tasks", rid)
        self.db.log_change("tasks", rid, "create", "добавлена " + self._plain("task", row), after=row)
        self.last["task"] = rid
        lines = [f"📝 Задача: <b>{esc(text)}</b>"]
        if due:
            lines.append(f"📅 Срок: {util.fmt_due(due)}")
        if remind and not reminded:
            lines.append(f"⏰ Напомню: {util.fmt_due(remind)}")
        elif not due:
            lines.append("Без даты — напоминать не буду. Скажи «напомни …», если нужна дата.")
        return Reply("\n".join(lines) + note, [[("✅ Готово", f"t:done:{rid}"), ("🗑 Удалить", f"t:del:{rid}")]])

    def a_edit_task(self, a, raw):
        row = self._resolve("task", {k: a.get(k) for k in ("id", "name", "query", "old_text") if a.get(k)} or a, only_open=True)
        upd = {}
        new_text = _txt(a.get("new_text") or (a.get("text") if a.get("id") else ""), 200)
        if new_text:
            upd["text"] = new_text
        if a.get("due"):
            upd["due"] = util.norm_due(a.get("due"))
        if a.get("due") or a.get("remind"):
            due = upd.get("due", row["due"])
            r, reminded, _ = self._calc_remind(due, a.get("remind"))
            upd.update(remind_at=r, reminded=reminded)
        if not upd:
            raise EngineError("Не понял, что изменить в задаче.")
        self.db.update("tasks", row["id"], **upd)
        after = self.db.get("tasks", row["id"])
        self.db.log_change("tasks", row["id"], "update", "изменена " + self._plain("task", after), before=row, after=after)
        self.last["task"] = row["id"]
        lines = [f"✏️ Задача: <b>{esc(after['text'])}</b>"]
        if after["due"]:
            lines.append(f"📅 Срок: {util.fmt_due(after['due'])}")
        if after["remind_at"] and not after["reminded"]:
            lines.append(f"⏰ Напомню: {util.fmt_due(after['remind_at'])}")
        return Reply("\n".join(lines), [[("✅ Готово", f"t:done:{row['id']}")]])

    def finish_task(self, tid: int) -> Reply:
        row = self.db.live("tasks", tid)
        if not row:
            return Reply("Такой задачи уже нет.", clear=True)
        if row["done"]:
            return Reply("Эта задача уже выполнена ✅", clear=True)
        self.db.update("tasks", tid, done=1, done_at=util.stamp())
        after = self.db.get("tasks", tid)
        self.db.log_change("tasks", tid, "update", "выполнена " + self._plain("task", row), before=row, after=after)
        return Reply(f"✅ Готово: {esc(row['text'])}", clear=True)

    def a_done(self, a, raw):
        row = self._resolve("task", a, only_open=True)
        return self.finish_task(row["id"])

    def snooze_task(self, tid: int, mode: str) -> Reply:
        row = self.db.live("tasks", tid)
        if not row or row["done"]:
            return Reply("Эта задача уже закрыта.", clear=True)
        n = util.now()
        if mode == "hour":
            target = n + timedelta(hours=1)
        else:
            tm = util.to_datetime(row["remind_at"])
            hh, mm = (tm.hour, tm.minute) if tm else (config.DEFAULT_REMIND_HOUR, 0)
            target = (n + timedelta(days=1)).replace(hour=hh, minute=mm)
        r = target.strftime("%Y-%m-%d %H:%M")
        self.db.update("tasks", tid, remind_at=r, reminded=0)
        return Reply(f"⏳ Напомню снова: {util.fmt_due(r)} — {esc(row['text'])}", clear=True)

    # ------------------------------------------------------------------ долги
    @staticmethod
    def _direction(x) -> str | None:
        s = str(x or "").casefold()
        if s in ("owed_to_me", "lent", "gave", "to_me", "они_мне", "мне"):
            return "owed_to_me"
        if s in ("i_owe", "borrowed", "took", "from_me", "я_должен", "я"):
            return "i_owe"
        return None

    def a_debt(self, a, raw):
        person = _txt(a.get("person") or a.get("name"), 40)
        d = self._direction(a.get("direction"))
        amount = _f(a.get("amount"))
        if not person or not amount:
            raise EngineError("Не понял долг: нужно имя и сумма. Например: «одолжил Васе 500».")
        if not d:
            raise EngineError("Не понял, кто кому должен. Напиши: «одолжил Васе 500» или «занял у мамы 2000».")
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        base, _ = self.rates.to_base(amount, cur)
        rid = self.db.insert("debts", person=person, direction=d, amount=base, note=_txt(a.get("note"), 100),
                             created_at=util.stamp())
        row = self.db.get("debts", rid)
        self.db.log_change("debts", rid, "create", "добавлен " + self._plain("debt", row), after=row)
        self.last["debt"] = rid
        tot = self._person_remaining(person, d)
        if d == "owed_to_me":
            head = f"💳 {esc(person)} должен тебе {util.money_pair(amount, cur, base)}"
        else:
            head = f"💳 Ты должен {esc(person)}: {util.money_pair(amount, cur, base)}"
        tail = f"\nВсего по {esc(person)}: {money(tot)}" if tot > base + 0.005 else ""
        return Reply(head + tail + f"\nСвободно: {money(calc.balance(self.db))}",
                     [[("🗑 Удалить", f"d:del:{rid}")]])

    def _person_remaining(self, person: str, d: str | None = None) -> float:
        tot = 0.0
        for r in self.db.q("SELECT * FROM debts WHERE deleted=0 AND closed=0"):
            if (d is None or r["direction"] == d) and util.same_person(person, r["person"]):
                tot += r["amount"] - r["paid"]
        return tot

    def _open_debts(self, person: str, d: str | None):
        rows = [dict(r) for r in self.db.q("SELECT * FROM debts WHERE deleted=0 AND closed=0 ORDER BY id")
                if util.same_person(person, r["person"]) and (d is None or r["direction"] == d)]
        if not rows:
            raise EngineError(f"Открытого долга с «{person}» не нашёл. Посмотри: «долги».")
        return rows

    def a_debt_pay(self, a, raw):
        person = _txt(a.get("person") or a.get("name"), 40)
        if not person:
            raise EngineError("Чей долг? Назови имя.")
        rows = self._open_debts(person, self._direction(a.get("direction")))
        dirs = {r["direction"] for r in rows}
        if len(dirs) > 1:
            raise EngineError("С этим человеком есть долги в обе стороны — уточни, кто кому.")
        direction = dirs.pop()
        remaining = sum(r["amount"] - r["paid"] for r in rows)
        amount = _f(a.get("amount"))
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        pay = remaining if not amount else self.rates.to_base(amount, cur)[0]
        extra = ""
        if pay > remaining + 0.005:
            extra = f"\n(Лишние {money(pay - remaining)} не учёл — долг был меньше.)"
            pay = remaining
        left = pay
        for r in rows:
            if left <= 0.004:
                break
            take = min(left, r["amount"] - r["paid"])
            before = dict(r)
            new_paid = round(r["paid"] + take, 2)
            closed = 1 if new_paid >= r["amount"] - 0.005 else 0
            self.db.update("debts", r["id"], paid=new_paid, closed=closed)
            self.db.log_change("debts", r["id"], "update", f"платёж по долгу {r['person']} {num(take)}",
                               before=before, after=self.db.get("debts", r["id"]))
            left -= take
        rest = self._person_remaining(person, direction)
        name = esc(rows[0]["person"])
        verb = f"{name} вернул" if direction == "owed_to_me" else f"Ты отдал {name}"
        tail = f"Осталось: {money(rest)}" if rest > 0.005 else "Долг закрыт 🎉"
        return Reply(f"✅ {verb} {money(pay)}. {tail}{extra}\nСвободно: {money(calc.balance(self.db))}")

    def a_debt_forgive(self, a, raw):
        person = _txt(a.get("person") or a.get("name"), 40)
        rows = self._open_debts(person, self._direction(a.get("direction")))
        total = 0.0
        for r in rows:
            before = dict(r)
            total += r["amount"] - r["paid"]
            self.db.update("debts", r["id"], closed=1, forgiven=1)
            self.db.log_change("debts", r["id"], "update", f"списан долг {r['person']}",
                               before=before, after=self.db.get("debts", r["id"]))
        return Reply(f"🤝 Списал долг «{esc(rows[0]['person'])}»: {money(total)}. Деньги не вернулись, баланс не меняется.")

    # ------------------------------------------------------------------ заначки и цели
    @staticmethod
    def _stash_name(s) -> str:
        t = _txt(s, 40)
        t = re.sub(r"^(на|в|для|в\s+копилку|в\s+заначку)\s+", "", t, flags=re.I)
        return (t[:1].upper() + t[1:]) if t else ""

    def a_stash(self, a, raw):
        name = self._stash_name(a.get("name")) or "Заначка"
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        target = _f(a.get("target"))
        deadline = util.parse_human_date(a.get("deadline")) if a.get("deadline") else None
        ex = self.db.find_stash(name)
        if ex and util.match_score(name, ex["name"]) >= 100:
            upd = {}
            if target:
                upd["target"] = target
            if deadline:
                upd["deadline"] = deadline.isoformat()
            if not upd:
                raise EngineError(f"Копилка «{ex['name']}» уже есть.")
            before = dict(ex)
            self.db.update("stashes", ex["id"], **upd)
            self.db.log_change("stashes", ex["id"], "update", f"обновлена копилка {ex['name']}",
                               before=before, after=self.db.get("stashes", ex["id"]))
            return Reply(f"🎯 Обновил цель «{esc(ex['name'])}».\n" + self._stash_progress(self.db.get("stashes", ex["id"])))
        rid = self.db.insert("stashes", name=name, target=target, deadline=deadline.isoformat() if deadline else None,
                             currency=cur, created_at=util.stamp())
        row = self.db.get("stashes", rid)
        self.db.log_change("stashes", rid, "create", "создана " + self._plain("stash", row), after=row)
        self.last["stash"] = rid
        txt = f"🐷 Создал {'цель' if target else 'копилку'} «{esc(name)}»"
        if target:
            txt += f" на {money(target, cur)}"
        if deadline:
            txt += f" до {util.fmt_short(deadline)}"
        return Reply(txt + ".\nПополнять: «отложил 2000 в " + esc(name.lower()) + "».")

    def _stash_progress(self, s: dict) -> str:
        amt = calc.stash_amount(self.db, s["id"])
        line = f"🐷 {esc(s['name'])}: <b>{money(amt, s['currency'])}</b>"
        if s["currency"] != util.base_cur():
            v = calc.stash_value_base(self.db, self.rates, s)
            if v is not None:
                line += f" (≈ {money(v)})"
        if s["target"]:
            frac = amt / s["target"]
            line += f"\n{util.bar(frac)} {frac * 100:.0f}% из {money(s['target'], s['currency'])}"
            if amt >= s["target"]:
                line += "\n🎉 Цель достигнута!"
        return line

    def a_stash_move(self, a, raw):
        amount = _f(a.get("amount"))
        if not amount or amount <= 0:
            raise EngineError("Не понял сумму для копилки.")
        out = str(a.get("direction") or "in").casefold() in ("out", "take", "withdraw", "из")
        cur_in = util.norm_currency(a.get("currency"))
        name = self._stash_name(a.get("name"))
        s = self.db.find_stash(name) if name else None
        created = ""
        if not s:
            actives = [dict(r) for r in self.db.q("SELECT * FROM stashes WHERE deleted=0 AND closed=0")]
            if not name:
                if len(actives) == 1:
                    s = actives[0]
                elif len(actives) > 1:
                    default = [x for x in actives if x["name"].casefold() == "заначка"]
                    if default:
                        s = default[0]
                    else:
                        raise EngineError("В какую копилку? Есть: " + ", ".join(x["name"] for x in actives))
            if not s:
                if out:
                    raise EngineError("Такой копилки нет.")
                nm = name or "Заначка"
                rid = self.db.insert("stashes", name=nm, currency=cur_in or util.base_cur(), created_at=util.stamp())
                s = self.db.get("stashes", rid)
                self.db.log_change("stashes", rid, "create", "создана " + self._plain("stash", s), after=s)
                created = f"🆕 Создал копилку «{esc(nm)}»\n"
        cur_in = cur_in or s["currency"]
        delta = amount if cur_in == s["currency"] else self.rates.convert(amount, cur_in, s["currency"])
        have = calc.stash_amount(self.db, s["id"])
        if out and delta > have + 0.005:
            raise EngineError(f"В «{s['name']}» только {money(have, s['currency'])}.")
        sign = -1 if out else 1
        base_delta = sign * self.rates.to_base(delta, s["currency"])[0]
        mid = self.db.insert("stash_moves", stash_id=s["id"], delta=sign * delta, base_delta=base_delta,
                             date=util.today().isoformat(), note=_txt(a.get("note"), 60), created_at=util.stamp())
        self.db.log_change("stash_moves", mid, "create",
                           f"{'взято из' if out else 'отложено в'} «{s['name']}» {num(delta)} {s['currency']}")
        self.last["stash"] = s["id"]
        s = self.db.get("stashes", s["id"])
        head = (f"🐷 Взял из «{esc(s['name'])}» {money(delta, s['currency'])}" if out
                else f"🐷 Отложил {money(delta, s['currency'])} в «{esc(s['name'])}»")
        return Reply(created + head + "\n" + self._stash_progress(s) + f"\nСвободно: {money(calc.balance(self.db))}")

    # ------------------------------------------------------------------ подписки, платежи, сроки
    def a_sub(self, a, raw):
        name = _txt(a.get("name"), 50)
        if not name:
            raise EngineError("Как назвать платёж? Например: «Claude Pro 20$ каждое 14-е».")
        kind = "income" if str(a.get("kind") or "").casefold() == "income" else "expense"
        amount = _f(a.get("amount"), 0.0) or 0.0
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        period = str(a.get("period") or ("once" if not amount else "month")).casefold()
        if period not in ("month", "year", "once"):
            period = "month"
        d = util.parse_human_date(a.get("next_due"))
        if not d and a.get("day"):
            try:
                d = calc.next_occurrence(int(a["day"]), util.today())
            except (TypeError, ValueError):
                d = None
        if not d:
            raise EngineError("Когда следующий платёж или срок? Назови дату или число месяца (например «каждое 14-е»).")
        for r in self.db.q("SELECT * FROM recurring WHERE deleted=0"):
            if r["name"].casefold() == name.casefold() and r["kind"] == kind:
                return self.a_sub_edit({**a, "id": r["id"], "next_due": d.isoformat()}, raw)
        cat = a.get("category") or ("Зарплата" if kind == "income" else "Подписки")
        cname, _ = self.db.ensure_category(cat, a.get("emoji"))
        rid = self.db.insert("recurring", name=name, kind=kind, amount=amount, currency=cur, period=period,
                             next_due=d.isoformat(), anchor=d.day, category=cname, created_at=util.stamp())
        row = self.db.get("recurring", rid)
        self.db.log_change("recurring", rid, "create", "добавлен " + self._plain("sub", row), after=row)
        self.last["sub"] = rid
        if not amount:
            tail = "Напомню за 3 и за 1 день до срока."
        elif kind == "income":
            tail = "Напомню за 3 и за 1 день, а в этот день спрошу «пришло?»."
        else:
            tail = "Напомню за 3 и за 1 день, а в день платежа спрошу «оплатил?»."
        return Reply("🔁 Запомнил:\n" + views.sub_line(row, self.rates, util.today()) + "\n" + tail)

    def a_sub_edit(self, a, raw):
        row = self._resolve("sub", a)
        upd = {}
        if a.get("new_name"):
            upd["name"] = _txt(a["new_name"], 50)
        if a.get("amount") not in (None, ""):
            upd["amount"] = _f(a.get("amount"), 0.0) or 0.0
        if a.get("currency"):
            c = util.norm_currency(a["currency"])
            if c:
                upd["currency"] = c
        if str(a.get("period") or "").casefold() in ("month", "year", "once"):
            upd["period"] = str(a["period"]).casefold()
        if a.get("category"):
            upd["category"] = self.db.ensure_category(a["category"])[0]
        d = util.parse_human_date(a.get("next_due"))
        if not d and a.get("day"):
            try:
                d = calc.next_occurrence(int(a["day"]), util.today())
            except (TypeError, ValueError):
                d = None
        if d:
            upd.update(next_due=d.isoformat(), anchor=d.day, notified="", nag=0)
        act = _bool(a.get("active"))
        if act is not None:
            upd["active"] = 1 if act else 0
        if not upd:
            raise EngineError("Не понял, что изменить в платеже.")
        self.db.update("recurring", row["id"], **upd)
        after = self.db.get("recurring", row["id"])
        self.db.log_change("recurring", row["id"], "update", "изменён " + self._plain("sub", after), before=row, after=after)
        self.last["sub"] = row["id"]
        return Reply("✏️ Обновил:\n" + views.sub_line(after, self.rates, util.today()))

    def a_sub_paid(self, a, raw):
        row = self._resolve("sub", a)
        amount = _f(a.get("amount"))
        return self.confirm_recurring(row["id"], (amount, util.norm_currency(a.get("currency"))) if amount else None)

    def confirm_recurring(self, sid: int, override=None) -> Reply:
        r = self.db.live("recurring", sid)
        if not r:
            return Reply("Такого платежа уже нет.", clear=True)
        d = util.parse_date(r["next_due"]) or util.today()
        today = util.today()
        amount, cur = r["amount"], r["currency"]
        if override and override[0]:
            amount = override[0]
            cur = override[1] or r["currency"]
        tx_id, line = None, ""
        if amount:
            base, _ = self.rates.to_base(amount, cur)
            tx_id = self.db.insert("tx", kind=r["kind"], amount=base, currency=cur, orig_amount=amount,
                                   category=r["category"] or "Другое", description=r["name"], date=today.isoformat(),
                                   raw=f"recurring:{sid}", created_at=util.stamp())
            line = ("✅ Записал расход: " if r["kind"] == "expense" else "💰 Записал доход: ") + \
                   f"{esc(r['name'])} — {util.money_pair(amount, cur, base)}"
            self.last["tx"] = tx_id
        before = dict(r)
        upd = self._advance(r, d, today)
        self.db.update("recurring", sid, **upd)
        after = self.db.get("recurring", sid)
        self.db.log_change("paid", sid, "update", f"оплата/продление «{r['name']}»", before=before,
                           after={"tx_id": tx_id})
        return Reply(line + self._next_line(after), clear=True)

    @staticmethod
    def _advance(r: dict, d, today) -> dict:
        if r["period"] == "once":
            return {"active": 0, "notified": "", "nag": 0}
        nd = calc.advance_due(d, r["period"], r["anchor"])
        for _ in range(48):
            if nd > today:
                break
            nd = calc.advance_due(nd, r["period"], r["anchor"])
        return {"next_due": nd.isoformat(), "notified": "", "nag": 0}

    @staticmethod
    def _next_line(after: dict) -> str:
        if not after["active"]:
            return "\nБольше не напоминаю. Если продлил — скажи новую дату: «домен до 15 марта 2028»."
        nd = util.parse_date(after["next_due"])
        return f"\nСледующий раз: {util.fmt_short(nd)} ({util.days_phrase((nd - util.today()).days)})"

    def skip_recurring(self, sid: int) -> Reply:
        r = self.db.live("recurring", sid)
        if not r:
            return Reply("Такого платежа уже нет.", clear=True)
        d = util.parse_date(r["next_due"]) or util.today()
        before = dict(r)
        self.db.update("recurring", sid, **self._advance(r, d, util.today()))
        after = self.db.get("recurring", sid)
        self.db.log_change("recurring", sid, "update", f"пропущен «{r['name']}»", before=before, after=after)
        return Reply(f"⏭ Пропустил «{esc(r['name'])}», в записи ничего не добавлял." + self._next_line(after), clear=True)

    def disable_recurring(self, sid: int) -> Reply:
        r = self.db.live("recurring", sid)
        if not r:
            return Reply("Такого платежа уже нет.", clear=True)
        self.db.update("recurring", sid, active=0)
        self.db.log_change("recurring", sid, "update", f"отключён «{r['name']}»", before=r,
                           after=self.db.get("recurring", sid))
        return Reply(f"⏸ Отключил «{esc(r['name'])}». Вернуть: «верни платёж» или скажи новую дату.", clear=True)

    def extend_recurring(self, sid: int, months: int) -> Reply:
        r = self.db.live("recurring", sid)
        if not r:
            return Reply("Такого срока уже нет.", clear=True)
        base = max(util.parse_date(r["next_due"]) or util.today(), util.today())
        y, m = util.add_months(base, months)
        nd = util.occurrence(base.day, y, m)
        period = "year" if months == 12 else "month"
        before = dict(r)
        self.db.update("recurring", sid, next_due=nd.isoformat(), anchor=nd.day, period=period,
                       active=1, notified="", nag=0)
        self.db.log_change("recurring", sid, "update", f"продлён «{r['name']}»", before=before,
                           after=self.db.get("recurring", sid))
        return Reply(f"🔄 Продлил «{esc(r['name'])}» до {util.fmt_short(nd)}.", clear=True)

    def recurring_prompt(self, r: dict, overdue_days: int = 0) -> Reply:
        base = calc.sub_base_amount(self.rates, r)
        rid = r["id"]
        late = f" (просрочено на {overdue_days} дн.)" if overdue_days > 0 else ""
        if not r["amount"]:
            return Reply(f"⏳ Срок{late}: <b>{esc(r['name'])}</b>. Что делаем?",
                         [[("🔄 Продлил на месяц", f"rp:ext_m:{rid}"), ("🔄 Продлил на год", f"rp:ext_y:{rid}")],
                          [("🗑 Больше не нужно", f"rp:off:{rid}")]])
        amt = util.money_pair(r["amount"], r["currency"], base) if base is not None else money(r["amount"], r["currency"])
        if r["kind"] == "income":
            return Reply(f"💼 Сегодня «{esc(r['name'])}»{late}: {amt}. Пришло?",
                         [[("✅ Пришло", f"rp:ok:{rid}"), ("✏️ Другая сумма", f"rp:oth:{rid}")],
                          [("⏭ Пропустить", f"rp:skip:{rid}"), ("⏸ Отключить", f"rp:off:{rid}")]])
        return Reply(f"🔔 Сегодня платёж{late}: <b>{esc(r['name'])}</b> — {amt}. Записать?",
                     [[("✅ Оплатил", f"rp:ok:{rid}"), ("✏️ Другая сумма", f"rp:oth:{rid}")],
                      [("⏭ Пропустить", f"rp:skip:{rid}"), ("⏸ Отключить", f"rp:off:{rid}")]])

    def recurring_heads_up(self, r: dict, days: int) -> Reply:
        base = calc.sub_base_amount(self.rates, r)
        when = util.days_phrase(days)
        if not r["amount"]:
            return Reply(f"⏳ {when.capitalize()} срок: <b>{esc(r['name'])}</b> ({util.fmt_short(util.parse_date(r['next_due']))}).")
        amt = util.money_pair(r["amount"], r["currency"], base) if base is not None else money(r["amount"], r["currency"])
        if r["kind"] == "income":
            return Reply(f"💼 {when.capitalize()} ожидается «{esc(r['name'])}»: {amt}.")
        text = f"🔁 {when.capitalize()} платёж: <b>{esc(r['name'])}</b> — {amt}."
        if base is not None:
            bal = calc.balance(self.db)
            text += (f"\n✅ Свободных денег хватает ({money(bal)})." if bal >= base
                     else f"\n⚠️ Свободных денег сейчас {money(bal)} — не хватает {money(base - bal)}.")
        return Reply(text, [[("✅ Оплатил сейчас", f"rp:ok:{r['id']}")]])

    # ------------------------------------------------------------------ дневник
    def a_journal(self, a, raw):
        text = _txt(a.get("text"), 300)
        if not text:
            raise EngineError("Что записать в дневник?")
        kind = str(a.get("kind") or "note").casefold()
        kind = kind if kind in JOURNAL_KINDS else "note"
        d = util.parse_human_date(a.get("date")) or util.today()
        rid = self.db.insert("journal", kind=kind, text=text, value=_f(a.get("value")), date=d.isoformat(),
                             created_at=util.stamp())
        row = self.db.get("journal", rid)
        self.db.log_change("journal", rid, "create", "дневник: " + text[:60], after=row)
        self.last["journal"] = rid
        icon = views.KIND_ICON.get(kind, "📓")
        val = f" ({num(row['value'])})" if row["value"] is not None else ""
        return Reply(f"📓 Записал: {icon} {esc(text)}{val}", [[("🗑 Удалить", f"j:del:{rid}")]])

    # ------------------------------------------------------------------ настройки и справочники
    def a_set_balance(self, a, raw):
        target = _f(a.get("amount"))
        if target is None:
            raise EngineError("Какая сумма сейчас у тебя на руках?")
        cur = util.norm_currency(a.get("currency")) or util.base_cur()
        target_base, _ = self.rates.to_base(target, cur)
        opening = float(self.db.setting("opening", "0") or 0)
        self.db.set_setting("opening", round(opening + target_base - calc.balance(self.db), 2))
        return Reply(f"💰 Принял: сейчас свободно <b>{money(calc.balance(self.db))}</b>. Дальше считаю от этого.")

    def a_set_budget(self, a, raw):
        amount = _f(a.get("amount"), 0.0) or 0.0
        cat = a.get("category")
        if not cat:
            if amount:
                self.db.set_setting("month_budget", amount)
                return Reply(f"🎯 Общий лимит на месяц: {money(amount)}. Предупрежу на 80% и 100%.")
            self.db.del_setting("month_budget")
            return Reply("Общий лимит на месяц убрал.")
        cname, _ = self.db.ensure_category(cat)
        self.db.exec("UPDATE categories SET budget=? WHERE name=?", (amount or None, cname))
        if amount:
            return Reply(f"🎯 Лимит на «{esc(cname)}»: {money(amount)} в месяц. Предупрежу на 80% и 100%.")
        return Reply(f"Лимит на «{esc(cname)}» убрал.")

    def _move_category(self, src: str, dst: str) -> int:
        n = self.db.one("SELECT COUNT(*) c FROM tx WHERE category=?", (src,))["c"]
        self.db.exec("UPDATE tx SET category=? WHERE category=?", (dst, src))
        self.db.exec("UPDATE recurring SET category=? WHERE category=?", (dst, src))
        return n

    def a_category(self, a, raw):
        op = str(a.get("op") or "rename").casefold()
        src = self.db.find_category(a.get("from") or "")
        if not src:
            raise EngineError("Не нашёл такую категорию.")
        to_name = _txt(a.get("to"), 30)
        if not to_name:
            raise EngineError("Как назвать?")
        dst = self.db.find_category(to_name)
        if dst and dst["name"] == src["name"]:
            raise EngineError("Это та же категория.")
        if dst or op == "merge":
            if not dst:
                dname, _ = self.db.ensure_category(to_name)
            else:
                dname = dst["name"]
            n = self._move_category(src["name"], dname)
            if src["budget"] and not (dst and dst["budget"]):
                self.db.exec("UPDATE categories SET budget=? WHERE name=?", (src["budget"], dname))
            self.db.exec("DELETE FROM categories WHERE name=?", (src["name"],))
            return Reply(f"🔀 Объединил «{esc(src['name'])}» → «{esc(dname)}» ({n} записей). Это нельзя отменить командой.")
        new = db_clean = to_name[:1].upper() + to_name[1:]
        self.db.exec("UPDATE categories SET name=? WHERE name=?", (new, src["name"]))
        self.db.exec("UPDATE tx SET category=? WHERE category=?", (new, src["name"]))
        self.db.exec("UPDATE recurring SET category=? WHERE category=?", (new, src["name"]))
        return Reply(f"✏️ Переименовал «{esc(src['name'])}» → «{esc(db_clean)}».")

    def a_rate(self, a, raw):
        cur = util.norm_currency(a.get("currency"))
        if not cur or cur == util.base_cur():
            raise EngineError("Для какой валюты курс? Например: «курс доллара 95».")
        v = a.get("value")
        if str(v).casefold() in ("auto", "авто", "none", "null", ""):
            self.rates.clear_manual(cur)
            cur_rate = self.rates.get(cur)
            return Reply(f"💱 {cur}: снова беру курс ЦБ" + (f" (сейчас {num(cur_rate)})." if cur_rate else
                                                            ". Пока курса нет — он подтянется автоматически."))
        value = _f(v)
        if not value or value <= 0:
            raise EngineError("Не понял курс. Пример: «курс доллара 95».")
        self.rates.set_manual(cur, value)
        return Reply(f"💱 Курс {cur} = {num(value)} {util.SYMBOLS.get(util.base_cur(), util.base_cur())} (задан вручную). "
                     f"Вернуть авто: «курс {cur} авто».")

    def a_settings(self, a, raw):
        key = str(a.get("key") or "").casefold()
        val = _bool(a.get("value"))
        names = {"morning": "Утренняя сводка", "weekly": "Итоги недели"}
        if key not in names or val is None:
            return Reply(views.settings_view(self.db))
        self.db.set_setting(key, "on" if val else "off")
        return Reply(f"{names[key]}: {'включена' if val else 'выключена'}.")

    def a_say(self, a, raw):
        t = _txt(a.get("text"), 600)
        return Reply(esc(t)) if t else None

    def a_backup(self, a=None, raw=""):
        return Reply(kind="backup")

    def a_query(self, a, raw):
        what = str(a.get("what") or "help").casefold()
        period = str(a.get("period") or "").casefold()
        period = PERIOD_ALIAS.get(period, period)
        if period not in util.PERIODS:
            period = ""
        if what in ("balance", "allowance"):
            return Reply(views.balance_view(self.db, self.rates))
        if what == "stats":
            cat = a.get("category")
            return Reply(views.stats_view(self.db, self.rates, period or "month", cat if cat else None))
        if what in ("tasks", "todo"):
            return Reply(views.tasks_view(self.db))
        if what == "debts":
            return Reply(views.debts_view(self.db))
        if what in ("stashes", "goals", "savings"):
            return Reply(views.stashes_view(self.db, self.rates))
        if what in ("subs", "subscriptions", "payments", "recurring"):
            return Reply(views.subs_view(self.db, self.rates))
        if what in ("recent", "list"):
            return Reply(views.recent_view(self.db))
        if what == "journal":
            return Reply(views.journal_view(self.db, a.get("kind"), period or None, a.get("text")))
        if what == "search":
            q = _txt(a.get("text") or a.get("query"), 80)
            if not q:
                raise EngineError("Что искать?")
            return Reply(views.search_view(self.db, self.rates, q))
        if what in ("brief", "morning"):
            return Reply(views.morning_brief(self.db, self.rates))
        if what == "weekly":
            return Reply(views.weekly_review(self.db, self.rates))
        if what == "history":
            return Reply(views.history_view(self.db))
        if what == "categories":
            return Reply(views.categories_view(self.db))
        if what == "rates":
            return Reply(views.rates_view(self.db, self.rates))
        if what == "settings":
            return Reply(views.settings_view(self.db))
        return Reply(views.help_text())

    # ------------------------------------------------------------------ кнопки
    def callback(self, data: str) -> list[Reply]:
        p = data.split(":")
        try:
            return self._callback(p)
        except EngineError as e:
            return [Reply("⚠️ " + esc(str(e)))]
        except NoRate as e:
            return [Reply(self._norate(e.code))]

    def _callback(self, p: list[str]) -> list[Reply]:
        head = p[0]
        if head == "x":
            self.pending = None
            return [Reply("Ок, ничего не меняю.", clear=True)]
        if head == "tx":
            op, rid = p[1], int(p[2])
            if op == "e":
                if not self.db.live("tx", rid):
                    return [Reply("Эта запись уже удалена.", clear=True)]
                return [Reply("Что изменить в записи #%d?" % rid, [
                    [("💲 Сумма", f"tx:f:{rid}:amount"), ("📂 Категория", f"tx:f:{rid}:category")],
                    [("📅 Дата", f"tx:f:{rid}:date"), ("📝 Описание", f"tx:f:{rid}:description")],
                    [("🔄 Расход ⇄ Доход", f"tx:f:{rid}:kind")],
                    [("✖️ Отмена", "x:cancel")]])]
            if op == "d":
                return [self.delete_row("tx", rid)]
            if op == "f":
                field_ = p[3]
                row = self.db.live("tx", rid)
                if not row:
                    return [Reply("Эта запись уже удалена.", clear=True)]
                if field_ == "kind":
                    return [self.apply_tx_edit(rid, {"kind": "income" if row["kind"] == "expense" else "expense"})]
                if field_ == "category":
                    names = self.db.category_names()
                    em = self.db.emojis()
                    rows, cur_row = [], []
                    for i, n in enumerate(names):
                        cur_row.append((f"{em.get(n, '🏷')} {n}", f"tx:c:{rid}:{i}"))
                        if len(cur_row) == 2:
                            rows.append(cur_row)
                            cur_row = []
                    if cur_row:
                        rows.append(cur_row)
                    rows.append([("🆕 Новая категория", f"tx:f:{rid}:newcat"), ("✖️ Отмена", "x:cancel")])
                    return [Reply("Выбери категорию:", rows)]
                if field_ in FIELD_PROMPTS:
                    self.pending = {"kind": "tx_field", "id": rid, "field": field_, "ts": util.now(), "fails": 0}
                    return [Reply(FIELD_PROMPTS[field_] + "\n(или «отмена»)")]
            if op == "c":
                names = self.db.category_names()
                idx = int(p[3])
                if not 0 <= idx < len(names):
                    return [Reply("Список категорий изменился, открой ✏️ заново.", clear=True)]
                return [self.apply_tx_edit(rid, {"category": names[idx]})]
        if head == "t":
            op, tid = p[1], int(p[2])
            if op == "done":
                return [self.finish_task(tid)]
            if op == "snz":
                return [self.snooze_task(tid, "hour")]
            if op == "tom":
                return [self.snooze_task(tid, "tomorrow")]
            if op == "del":
                return [self.delete_row("task", tid)]
        if head == "rp":
            op, sid = p[1], int(p[2])
            if op == "ok":
                return [self.confirm_recurring(sid)]
            if op == "skip":
                return [self.skip_recurring(sid)]
            if op == "off":
                return [self.disable_recurring(sid)]
            if op == "ext_m":
                return [self.extend_recurring(sid, 1)]
            if op == "ext_y":
                return [self.extend_recurring(sid, 12)]
            if op == "oth":
                if not self.db.live("recurring", sid):
                    return [Reply("Такого платежа уже нет.", clear=True)]
                self.pending = {"kind": "rp_amount", "id": sid, "ts": util.now(), "fails": 0}
                return [Reply("💲 Напиши фактическую сумму (например 450 или 20$)\n(или «отмена»)", clear=True)]
        if head == "r" and p[1] in ENT:
            return [self.restore_row(p[1], int(p[2]))]
        if head == "j" and p[1] == "del":
            return [self.delete_row("journal", int(p[2]))]
        if head == "d" and p[1] == "del":
            return [self.delete_row("debt", int(p[2]))]
        return [Reply("Эта кнопка устарела.", clear=True)]

    def resolve_pending(self, text: str) -> list[Reply] | None:
        p = self.pending
        if not p:
            return None
        if (util.now() - p["ts"]) > timedelta(minutes=10):
            self.pending = None
            return None
        t = text.strip()
        if t.casefold() in ("отмена", "cancel", "/cancel", "не надо", "нет"):
            self.pending = None
            return [Reply("Ок, ничего не меняю.")]
        if t.startswith("/"):
            self.pending = None
            return None
        try:
            if p["kind"] == "tx_field":
                f_ = p["field"]
                if f_ == "amount":
                    m = util.parse_money(t)
                    if not m:
                        return self._pending_fail(t, "Не понял сумму. Напиши число, например 450 или 20$.")
                    fields = {"amount": m[0], "currency": m[1]}
                elif f_ == "date":
                    if not util.parse_human_date(t):
                        return self._pending_fail(t, "Не понял дату. Напиши: вчера, 05.10 или 2026-10-05.")
                    fields = {"date": t}
                elif f_ == "newcat":
                    fields = {"category": t[:30]}
                else:
                    fields = {"description": t}
                self.pending = None
                return [self.apply_tx_edit(p["id"], fields)]
            if p["kind"] == "rp_amount":
                m = util.parse_money(t)
                if not m:
                    return self._pending_fail(t, "Не понял сумму. Напиши число, например 450 или 20$.")
                self.pending = None
                return [self.confirm_recurring(p["id"], (m[0], m[1]))]
        except EngineError as e:
            self.pending = None
            return [Reply("⚠️ " + esc(str(e)))]
        except NoRate as e:
            self.pending = None
            return [Reply(self._norate(e.code))]
        self.pending = None
        return None

    def _pending_fail(self, text: str, msg: str):
        self.pending["fails"] += 1
        if self.pending["fails"] >= 2 or not re.search(r"\d", text):
            self.pending = None
            return None  # пусть сообщение обработается как обычное
        return [Reply(msg + "\n(или «отмена»)")]

    # ------------------------------------------------------------------ контекст для ИИ
    def context(self) -> str:
        n = util.now()
        ref = n.date()
        cal = ", ".join(f"{(ref + timedelta(days=i)).isoformat()} {util.WD_SHORT[(ref + timedelta(days=i)).weekday()]}"
                        for i in range(1, 15))
        lines = [f"now: {n:%Y-%m-%d %H:%M}, {util.WD_FULL[ref.weekday()]}; base_currency: {util.base_cur()}",
                 f"next days: {cal}",
                 "categories: " + ", ".join(self.db.category_names())]
        st = [r["name"] for r in self.db.q("SELECT name FROM stashes WHERE deleted=0 AND closed=0")]
        if st:
            lines.append("stashes: " + ", ".join(st))
        dbt = [f"{r['person']}({r['direction']} {num(r['amount'] - r['paid'])})"
               for r in self.db.q("SELECT * FROM debts WHERE deleted=0 AND closed=0 LIMIT 12")]
        if dbt:
            lines.append("open debts: " + ", ".join(dbt))
        subs = [f"[{r['id']}] {r['name']} {num(r['amount'])} {r['currency']} due {r['next_due']}"
                for r in self.db.q("SELECT * FROM recurring WHERE deleted=0 AND active=1 ORDER BY next_due LIMIT 12")]
        if subs:
            lines.append("recurring: " + "; ".join(subs))
        tasks = [f"[{r['id']}] {r['text'][:40]}" + (f" ({r['due']})" if r["due"] else "")
                 for r in self.db.q("SELECT * FROM tasks WHERE deleted=0 AND done=0 ORDER BY id DESC LIMIT 10")]
        if tasks:
            lines.append("open tasks: " + "; ".join(tasks))
        tx = [f"[{r['id']}] {r['category']} {num(r['orig_amount'])} {r['currency']} {r['description'][:25]}".strip()
              for r in self.db.q("SELECT * FROM tx WHERE deleted=0 ORDER BY id DESC LIMIT 6")]
        if tx:
            lines.append("recent: " + "; ".join(tx))
        return "\n".join(lines)
