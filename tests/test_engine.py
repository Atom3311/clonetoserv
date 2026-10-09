import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calc  # noqa: E402
import db as dbm  # noqa: E402
import rates as rt  # noqa: E402
import util  # noqa: E402
from engine import Engine  # noqa: E402


def T(replies):
    return "\n".join(r.text for r in replies)


class EngineBase(unittest.TestCase):
    def setUp(self):
        util.set_now(datetime(2026, 10, 9, 20, 30))
        self.db = dbm.DB(":memory:")
        self.rates = rt.Rates(self.db, fetcher=lambda: {"USD": 95.0, "EUR": 103.0})
        self.rates.refresh()
        self.e = Engine(self.db, self.rates)

    def tearDown(self):
        util.set_now(None)

    def run_(self, *actions):
        return self.e.run(list(actions), "тест")

    def bal(self):
        return round(calc.balance(self.db), 2)


class MoneyTests(EngineBase):
    def test_balance_arithmetic(self):
        self.run_({"type": "set_balance", "amount": 12000})
        self.assertEqual(self.bal(), 12000)
        self.run_({"type": "expense", "amount": 300, "category": "Еда"})
        self.assertEqual(self.bal(), 11700)
        self.run_({"type": "income", "amount": 50000, "category": "Зарплата"})
        self.assertEqual(self.bal(), 61700)
        self.run_({"type": "expense", "amount": 20, "currency": "USD", "category": "Подписки"})
        self.assertEqual(self.bal(), 59800)
        self.run_({"type": "set_balance", "amount": 1000})
        self.assertEqual(self.bal(), 1000)

    def test_new_category_created_and_reused(self):
        r = self.run_({"type": "expense", "amount": 800, "category": "питомцы", "description": "корм"})
        self.assertIn("Новая категория", T(r))
        r = self.run_({"type": "expense", "amount": 200, "category": "Питомцы"})
        self.assertNotIn("Новая категория", T(r))
        self.assertEqual(self.db.q("SELECT COUNT(*) c FROM categories WHERE name LIKE 'Питом%'")[0]["c"], 1)

    def test_edit_recomputes_currency(self):
        self.run_({"type": "expense", "amount": 300, "category": "Еда"})
        self.run_({"type": "edit_tx", "id": "last", "amount": 10, "currency": "EUR"})
        row = self.db.get("tx", 1)
        self.assertEqual((row["amount"], row["currency"], row["orig_amount"]), (1030, "EUR", 10))
        self.run_({"type": "edit_tx", "id": 1, "category": "Транспорт", "date": "вчера", "description": "такси"})
        row = self.db.get("tx", 1)
        self.assertEqual((row["category"], row["date"], row["description"]), ("Транспорт", "2026-10-08", "такси"))
        self.run_({"type": "edit_tx", "id": 1, "kind": "income"})
        self.assertEqual(self.db.get("tx", 1)["kind"], "income")

    def test_bad_input_does_not_crash(self):
        for a in ({"type": "expense", "amount": "abc"}, {"type": "expense", "amount": -5},
                  {"type": "expense"}, {"type": "edit_tx", "id": 999, "amount": 5},
                  {"type": "nonsense"}, {"type": "task"}, {"type": "debt", "person": "X"},
                  {"type": "delete", "entity": "tx", "id": 999}, {"type": "stash_move", "amount": 5, "direction": "out"},
                  {"type": "query", "what": "garbage"}, "not a dict", {"type": "__import__('os')"}):
            try:
                self.e.run([a], "x")
            except Exception as ex:  # noqa: BLE001
                self.fail(f"{a!r} -> {ex!r}")
        self.assertEqual(self.db.one("SELECT COUNT(*) c FROM tx")["c"], 0)

    def test_missing_rate_gives_hint_and_writes_nothing(self):
        r = self.run_({"type": "expense", "amount": 10, "currency": "GBP", "category": "Еда"})
        self.assertIn("курс GBP", T(r))
        self.assertEqual(self.db.one("SELECT COUNT(*) c FROM tx")["c"], 0)
        self.run_({"type": "rate", "currency": "GBP", "value": 120})
        self.run_({"type": "expense", "amount": 10, "currency": "GBP", "category": "Еда"})
        self.assertEqual(self.db.get("tx", 1)["amount"], 1200)

    def test_html_is_escaped(self):
        r = self.run_({"type": "expense", "amount": 5, "category": "Еда", "description": "<b>x</b> & y"})
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", T(r))
        self.assertNotIn("<b>x</b>", T(r))

    def test_budget_alerts(self):
        self.run_({"type": "set_budget", "category": "Еда", "amount": 1000})
        self.assertNotIn("80%", T(self.run_({"type": "expense", "amount": 700, "category": "Еда"})))
        self.assertIn("80%", T(self.run_({"type": "expense", "amount": 150, "category": "Еда"})))
        self.assertIn("превышен", T(self.run_({"type": "expense", "amount": 300, "category": "Еда"})))
        self.assertNotIn("превышен", T(self.run_({"type": "expense", "amount": 10, "category": "Еда"})))
        self.run_({"type": "set_budget", "amount": 2000})
        self.assertIn("Общий лимит", T(self.run_({"type": "expense", "amount": 900, "category": "Другое"})))


class UndoTests(EngineBase):
    def test_undo_chain(self):
        self.run_({"type": "expense", "amount": 300, "category": "Еда"})
        self.run_({"type": "edit_tx", "id": "last", "amount": 400})
        self.assertEqual(self.db.get("tx", 1)["amount"], 400)
        self.assertIn("Отменил", T(self.run_({"type": "undo"})))
        self.assertEqual(self.db.get("tx", 1)["amount"], 300)
        self.run_({"type": "undo"})
        self.assertEqual(self.db.get("tx", 1)["deleted"], 1)
        self.assertEqual(self.bal(), 0)
        self.assertIn("нечего", T(self.run_({"type": "undo"})).lower())

    def test_delete_restore(self):
        self.run_({"type": "income", "amount": 1000, "category": "Зарплата"})
        r = self.run_({"type": "delete", "entity": "tx", "id": 1})
        self.assertEqual(self.bal(), 0)
        self.assertIn("r:tx:1", [d for row in r[0].kb for _, d in row])
        self.run_({"type": "restore", "id": 1})
        self.assertEqual(self.bal(), 1000)
        self.run_({"type": "undo"})
        self.assertEqual(self.bal(), 0)

    def test_undo_debt_payment(self):
        self.run_({"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 500})
        self.run_({"type": "debt_pay", "person": "Васе", "amount": 500})
        self.assertEqual(self.db.get("debts", 1)["closed"], 1)
        self.run_({"type": "undo"})
        row = self.db.get("debts", 1)
        self.assertEqual((row["paid"], row["closed"]), (0, 0))


class DebtTests(EngineBase):
    def test_lend_and_repay(self):
        self.run_({"type": "set_balance", "amount": 5000})
        r = self.run_({"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 500, "note": "пицца"})
        self.assertEqual(self.bal(), 4500)
        self.assertIn("Вася должен тебе", T(r))
        r = self.run_({"type": "debt_pay", "person": "Васи", "amount": 300})
        self.assertEqual(self.bal(), 4800)
        self.assertIn("Осталось: 200", T(r).replace(" ", " "))
        r = self.run_({"type": "debt_pay", "person": "Вася"})
        self.assertEqual(self.bal(), 5000)
        self.assertIn("закрыт", T(r))
        self.assertEqual(calc.debts_open(self.db), (0, 0))

    def test_overpay_is_capped(self):
        self.run_({"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 100})
        r = self.run_({"type": "debt_pay", "person": "Вася", "amount": 500})
        self.assertIn("Лишние", T(r))
        self.assertEqual(self.bal(), 0)

    def test_borrow_and_return(self):
        self.run_({"type": "debt", "person": "мама", "direction": "i_owe", "amount": 2000})
        self.assertEqual(self.bal(), 2000)
        self.run_({"type": "debt_pay", "person": "маме", "amount": 500, "direction": "i_owe"})
        self.assertEqual(self.bal(), 1500)
        self.assertEqual(calc.debts_open(self.db), (0, 1500))

    def test_forgive_keeps_the_loss(self):
        self.run_({"type": "set_balance", "amount": 1000})
        self.run_({"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 500})
        self.run_({"type": "debt_pay", "person": "Вася", "amount": 300})
        self.run_({"type": "debt_forgive", "person": "Вася"})
        self.assertEqual(self.bal(), 800)   # 1000 − 500 + 300; оставшиеся 200 уже не вернутся
        self.assertEqual(calc.debts_open(self.db), (0, 0))

    def test_unknown_person(self):
        self.assertIn("не нашёл", T(self.run_({"type": "debt_pay", "person": "Петя", "amount": 5})))


class StashTests(EngineBase):
    def test_auto_create_and_move(self):
        self.run_({"type": "set_balance", "amount": 10000})
        r = self.run_({"type": "stash_move", "name": "Заначка", "amount": 2000, "direction": "in"})
        self.assertIn("Создал копилку", T(r))
        self.assertEqual(self.bal(), 8000)
        r = self.run_({"type": "stash_move", "name": "заначку", "amount": 500, "direction": "out"})
        self.assertEqual(self.bal(), 8500)
        self.assertIn("1 500", T(r).replace(" ", " "))
        self.assertIn("только", T(self.run_({"type": "stash_move", "amount": 99999, "direction": "out"})))
        self.assertEqual(self.bal(), 8500)

    def test_goal_progress_and_deadline(self):
        self.run_({"type": "stash", "name": "на ноутбук", "target": 60000, "deadline": "2027-03-01"})
        r = self.run_({"type": "stash_move", "name": "ноутбук", "amount": 15000, "direction": "in"})
        self.assertIn("25%", T(r))
        from views import stashes_view
        self.assertIn("в месяц", stashes_view(self.db, self.rates))

    def test_currency_stash(self):
        self.run_({"type": "set_balance", "amount": 10000})
        self.run_({"type": "stash", "name": "Валютная", "currency": "USD"})
        self.run_({"type": "stash_move", "name": "Валютная", "amount": 50, "currency": "USD", "direction": "in"})
        self.assertEqual(self.bal(), 5250)
        self.rates.set_manual("USD", 100)
        self.assertAlmostEqual(calc.stash_total_value(self.db, self.rates), 5000)
        self.assertEqual(self.bal(), 5250, "баланс не должен прыгать от курса")
        self.run_({"type": "stash_move", "name": "Валютная", "amount": 20, "currency": "USD", "direction": "out"})
        self.assertEqual(self.bal(), 7250)
        # рублями в долларовую копилку — пересчитывается по курсу (1000 / 100 = 10 $)
        self.run_({"type": "stash_move", "name": "Валютная", "amount": 1000, "currency": "RUB", "direction": "in"})
        self.assertAlmostEqual(calc.stash_amount(self.db, 1), 40.0)
        # число без валюты = в валюте самой копилки
        self.run_({"type": "stash_move", "name": "Валютная", "amount": 5, "direction": "in"})
        self.assertAlmostEqual(calc.stash_amount(self.db, 1), 45.0)

    def test_delete_stash_returns_money(self):
        self.run_({"type": "stash_move", "name": "Заначка", "amount": 700, "direction": "in"})
        self.assertEqual(self.bal(), -700)
        self.run_({"type": "delete", "entity": "stash", "name": "Заначка"})
        self.assertEqual(self.bal(), 0)


class SubscriptionTests(EngineBase):
    def test_monthly_confirm_and_undo(self):
        r = self.run_({"type": "sub", "name": "Claude Pro", "amount": 20, "currency": "USD", "day": 14,
                       "category": "Подписки"})
        self.assertIn("14 окт", T(r))
        row = self.db.get("recurring", 1)
        self.assertEqual((row["next_due"], row["anchor"]), ("2026-10-14", 14))
        self.e.confirm_recurring(1)
        self.assertEqual(self.db.get("recurring", 1)["next_due"], "2026-11-14")
        tx = self.db.get("tx", 1)
        self.assertEqual((tx["amount"], tx["category"], tx["description"]), (1900, "Подписки", "Claude Pro"))
        self.e.run([{"type": "undo"}])
        self.assertEqual(self.db.get("recurring", 1)["next_due"], "2026-10-14")
        self.assertEqual(self.db.get("tx", 1)["deleted"], 1)

    def test_day_31_never_drifts(self):
        self.run_({"type": "sub", "name": "Аренда", "amount": 15000, "next_due": "2026-10-31"})
        seen = []
        for _ in range(4):
            self.e.confirm_recurring(1)
            seen.append(self.db.get("recurring", 1)["next_due"])
            util.set_now(datetime.fromisoformat(seen[-1] + " 10:00"))
        self.assertEqual(seen, ["2026-11-30", "2026-12-31", "2027-01-31", "2027-02-28"])

    def test_missed_months_are_skipped_not_replayed(self):
        self.run_({"type": "sub", "name": "Спорт", "amount": 2000, "next_due": "2026-06-10"})
        self.e.confirm_recurring(1)
        # июнь–сентябрь пропущены; ближайшая будущая дата (сегодня 9 окт) — 10 окт
        self.assertEqual(self.db.get("recurring", 1)["next_due"], "2026-10-10")
        self.assertEqual(self.db.one("SELECT COUNT(*) c FROM tx")["c"], 1, "за пропущенные месяцы записей не создаём")

    def test_custom_amount_and_skip(self):
        self.run_({"type": "sub", "name": "Интернет", "amount": 500, "next_due": "2026-10-09"})
        self.e.confirm_recurring(1, (650, None))
        self.assertEqual(self.db.get("tx", 1)["amount"], 650)
        self.run_({"type": "sub", "name": "Зарплата", "kind": "income", "amount": 40000, "next_due": "2026-10-09"})
        self.e.skip_recurring(2)
        self.assertEqual(self.db.one("SELECT COUNT(*) c FROM tx")["c"], 1)
        self.assertEqual(self.db.get("recurring", 2)["next_due"], "2026-11-09")

    def test_deadline_only(self):
        self.run_({"type": "sub", "name": "Домен", "amount": 0, "next_due": "2026-10-09"})
        row = self.db.get("recurring", 1)
        self.assertEqual((row["amount"], row["period"]), (0, "once"))
        self.e.extend_recurring(1, 12)
        row = self.db.get("recurring", 1)
        self.assertEqual((row["next_due"], row["period"], row["active"]), ("2027-10-09", "year", 1))
        self.e.disable_recurring(1)
        self.assertEqual(self.db.get("recurring", 1)["active"], 0)

    def test_duplicate_name_updates(self):
        self.run_({"type": "sub", "name": "Spotify", "amount": 169, "day": 22})
        self.run_({"type": "sub", "name": "spotify", "amount": 199, "day": 22})
        self.assertEqual(self.db.one("SELECT COUNT(*) c FROM recurring")["c"], 1)
        self.assertEqual(self.db.get("recurring", 1)["amount"], 199)

    def test_edit_by_fuzzy_name(self):
        self.run_({"type": "sub", "name": "Claude Pro", "amount": 20, "currency": "USD", "day": 14})
        self.run_({"type": "sub_edit", "query": "Claude", "amount": 22})
        self.assertEqual(self.db.get("recurring", 1)["amount"], 22)

    def test_no_date_asks(self):
        self.assertIn("Когда", T(self.run_({"type": "sub", "name": "X", "amount": 5})))

    def test_allowance(self):
        self.run_({"type": "set_balance", "amount": 10000})
        self.run_({"type": "sub", "name": "Зарплата", "kind": "income", "amount": 50000, "next_due": "2026-10-15"})
        self.run_({"type": "sub", "name": "Сервер", "amount": 300, "next_due": "2026-10-12"})
        self.run_({"type": "sub", "name": "Аренда", "amount": 15000, "next_due": "2026-10-20"})  # после зарплаты
        a = calc.allowance(self.db, self.rates)
        self.assertEqual((a["days"], a["upcoming"]), (6, 300))
        self.assertAlmostEqual(a["per_day"], (10000 - 300) / 6)
        self.assertEqual(calc.sub_monthly_total(self.db, self.rates), (15300, 0))


class TaskTests(EngineBase):
    def test_reminder_rules(self):
        self.run_({"type": "task", "text": "сдать отчёт", "due": "2026-10-10 10:00"})
        t = self.db.get("tasks", 1)
        self.assertEqual((t["remind_at"], t["reminded"]), ("2026-10-10 10:00", 0))
        self.run_({"type": "task", "text": "купить билет", "due": "2026-10-12"})
        self.assertEqual(self.db.get("tasks", 2)["remind_at"], "2026-10-12 09:00")
        r = self.run_({"type": "task", "text": "позвонить", "due": "2026-10-09 18:00"})
        self.assertEqual(self.db.get("tasks", 3)["reminded"], 1)
        self.assertIn("уже прошло", T(r))
        self.run_({"type": "task", "text": "когда-нибудь"})
        self.assertIsNone(self.db.get("tasks", 4)["remind_at"])
        self.run_({"type": "task", "text": "за час до", "due": "2026-10-11 15:00", "remind": "2026-10-11 14:00"})
        self.assertEqual(self.db.get("tasks", 5)["remind_at"], "2026-10-11 14:00")

    def test_done_edit_snooze(self):
        self.run_({"type": "task", "text": "сдать отчёт", "due": "2026-10-10 10:00"})
        self.run_({"type": "edit_task", "query": "отчёт", "due": "2026-10-11 12:00"})
        self.assertEqual(self.db.get("tasks", 1)["remind_at"], "2026-10-11 12:00")
        self.e.snooze_task(1, "hour")
        self.assertEqual(self.db.get("tasks", 1)["remind_at"], "2026-10-09 21:30")
        self.e.snooze_task(1, "tomorrow")
        self.assertEqual(self.db.get("tasks", 1)["remind_at"], "2026-10-10 21:30")
        self.assertIn("Готово", T(self.run_({"type": "done", "query": "отчёт"})))
        self.assertIn("уже выполнена", T(self.e.callback("t:done:1")))
        self.run_({"type": "undo"})
        self.assertEqual(self.db.get("tasks", 1)["done"], 0)


class MiscTests(EngineBase):
    def test_categories_merge_rename(self):
        self.run_({"type": "expense", "amount": 100, "category": "Кафе"})
        self.run_({"type": "expense", "amount": 200, "category": "Еда"})
        self.run_({"type": "category", "op": "merge", "from": "Кафе", "to": "Еда"})
        self.assertEqual(calc.sum_tx(self.db, "expense", category="Еда"), 300)
        self.assertIsNone(self.db.find_category("Кафе"))
        self.run_({"type": "category", "op": "rename", "from": "Еда", "to": "Питание"})
        self.assertEqual(calc.sum_tx(self.db, "expense", category="Питание"), 300)
        self.run_({"type": "delete", "entity": "category", "name": "Питание"})
        self.assertEqual(calc.sum_tx(self.db, "expense", category="Другое"), 300)

    def test_every_view_renders(self):
        self.run_({"type": "set_balance", "amount": 5000},
                  {"type": "expense", "amount": 300, "category": "Еда", "description": "шаурма"},
                  {"type": "income", "amount": 1000, "category": "Зарплата"},
                  {"type": "task", "text": "сдать отчёт", "due": "2026-10-08"},
                  {"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 500},
                  {"type": "stash", "name": "Ноутбук", "target": 60000, "deadline": "2027-03-01"},
                  {"type": "sub", "name": "Claude", "amount": 20, "currency": "USD", "day": 14},
                  {"type": "journal", "kind": "workout", "text": "жим 60 3x8"})
        for what in ("balance", "stats", "tasks", "debts", "stashes", "subs", "recent", "journal", "search",
                     "history", "categories", "rates", "settings", "help", "brief", "weekly"):
            r = self.run_({"type": "query", "what": what, "text": "шаурма"})
            self.assertTrue(r and r[0].text, what)
        for p in util.PERIODS:
            self.assertTrue(self.run_({"type": "query", "what": "stats", "period": p})[0].text, p)
        r = self.run_({"type": "query", "what": "stats", "category": "Еда", "period": "week"})
        self.assertIn("шаурма", r[0].text)
        r = self.run_({"type": "query", "what": "search", "text": "шаурме"})
        self.assertIn("шаурма", r[0].text)

    def test_context_for_llm(self):
        self.run_({"type": "task", "text": "сдать отчёт", "due": "2026-10-10 10:00"},
                  {"type": "expense", "amount": 300, "category": "Еда", "description": "шаурма"})
        c = self.e.context()
        for needle in ("2026-10-10 сб", "Еда", "сдать отчёт", "шаурма", "base_currency: RUB"):
            self.assertIn(needle, c)

    def test_settings_toggle(self):
        self.run_({"type": "settings", "key": "morning", "value": "off"})
        self.assertEqual(self.db.setting("morning"), "off")
        self.run_({"type": "settings", "key": "weekly", "value": "off"})
        self.assertEqual(self.db.setting("weekly"), "off")

    def test_journal(self):
        r = self.run_({"type": "journal", "kind": "workout", "text": "жим 60 3x8", "value": 60})
        self.assertIn("🏋️", T(r))
        r = self.run_({"type": "journal", "kind": "weird", "text": "что-то"})
        self.assertEqual(self.db.get("journal", 2)["kind"], "note")


if __name__ == "__main__":
    unittest.main()


class HtmlSafetyTests(EngineBase):
    def test_validator_is_not_vacuous(self):
        from tests.mocks import html_problems
        self.assertTrue(html_problems("a <b>x"))
        self.assertTrue(html_problems("a < b"))
        self.assertTrue(html_problems("Tom & Jerry"))
        self.assertTrue(html_problems("<script>x</script>"))
        self.assertEqual(html_problems("<b>ok</b> &amp; <i>fine</i> &lt;tag&gt;"), [])

    def test_hostile_text_everywhere(self):
        from tests.mocks import html_problems
        evil = '<i>&"\'<script>x</script> & <b'
        self.run_({"type": "expense", "amount": 5, "category": evil, "description": evil},
                  {"type": "task", "text": evil, "due": "2026-10-09 23:00"},
                  {"type": "debt", "person": evil, "direction": "owed_to_me", "amount": 50, "note": evil},
                  {"type": "stash", "name": evil, "target": 100},
                  {"type": "stash_move", "name": evil, "amount": 10, "direction": "in"},
                  {"type": "sub", "name": evil, "amount": 10, "next_due": "2026-10-10"},
                  {"type": "sub", "name": evil + "2", "amount": 0, "next_due": "2026-10-09"},
                  {"type": "sub", "name": evil + "3", "kind": "income", "amount": 10, "next_due": "2026-10-20"},
                  {"type": "journal", "kind": "note", "text": evil},
                  {"type": "say", "text": evil})
        self.run_({"type": "set_budget", "category": evil, "amount": 1})
        bad = []
        for what in ("balance", "stats", "tasks", "debts", "stashes", "subs", "recent", "journal", "search",
                     "history", "categories", "rates", "settings", "brief", "weekly"):
            for r in self.run_({"type": "query", "what": what, "text": "script", "category": evil}):
                bad += html_problems(r.text)
        for r in self.e.recurring_prompt(dict(self.db.get("recurring", 1)), 2) and [
                self.e.recurring_prompt(dict(self.db.get("recurring", i)), 2) for i in (1, 2, 3)] + [
                self.e.recurring_heads_up(dict(self.db.get("recurring", i)), 3) for i in (1, 2, 3)]:
            bad += html_problems(r.text)
        self.assertEqual(bad, [])
