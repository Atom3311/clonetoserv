import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import calc  # noqa: E402
import db as dbm  # noqa: E402
import quick  # noqa: E402
import rates as rt  # noqa: E402
import util  # noqa: E402
import llm  # noqa: E402


class UtilTests(unittest.TestCase):
    def test_parse_money(self):
        cases = {"300": (300, None), "20$": (20, "USD"), "$20": (20, "USD"), "15 евро": (15, "EUR"),
                 "50к": (50000, None), "1 500,50 р": (1500.5, "RUB"), "2 тыс": (2000, None),
                 "20 долларов": (20, "USD"), "100 usd": (100, "USD"), "3500₽": (3500, "RUB"),
                 "1.5к": (1500, None), "10 евро.": (10, "EUR")}
        for s, exp in cases.items():
            self.assertEqual(util.parse_money(s), exp, s)
        for s in ("шаурма", "abc 5", "0", "-5", "", "300 шаурма"):
            self.assertIsNone(util.parse_money(s), s)

    def test_human_dates(self):
        ref = date(2026, 10, 9)
        self.assertEqual(util.parse_human_date("вчера", ref), date(2026, 10, 8))
        self.assertEqual(util.parse_human_date("05.10", ref), date(2026, 10, 5))
        self.assertEqual(util.parse_human_date("05.10.25", ref), date(2025, 10, 5))
        self.assertEqual(util.parse_human_date("2026-10-05", ref), date(2026, 10, 5))
        self.assertIsNone(util.parse_human_date("когда-нибудь", ref))
        self.assertIsNone(util.parse_human_date("31.02", ref))

    def test_occurrence_and_months(self):
        self.assertEqual(util.occurrence(31, 2026, 2), date(2026, 2, 28))
        self.assertEqual(util.add_months(date(2026, 12, 15), 1), (2027, 1))
        self.assertEqual(calc.advance_due(date(2026, 1, 31), "month", 31), date(2026, 2, 28))
        self.assertEqual(calc.advance_due(date(2026, 2, 28), "month", 31), date(2026, 3, 31))
        self.assertEqual(calc.advance_due(date(2028, 2, 29), "year", 29), date(2029, 2, 28))

    def test_fuzzy_names(self):
        self.assertTrue(util.same_person("Вася", "Васе"))
        self.assertTrue(util.same_person("Васи", "Вася"))
        self.assertFalse(util.same_person("Вася", "Вова"))
        self.assertGreater(util.match_score("отчёт", "сдать отчёт"), 0)
        self.assertEqual(util.match_score("ноутбук", "Ноутбук"), 100)

    def test_formatting(self):
        util.set_now(datetime(2026, 10, 9, 12, 0))
        self.assertEqual(util.money(1234567), "1 234 567 ₽")
        self.assertEqual(util.money(20, "USD"), "20 $")
        self.assertEqual(util.money(10.5), "10,50 ₽")
        self.assertEqual(util.days_phrase(3), "через 3 дня")
        self.assertEqual(util.days_phrase(5), "через 5 дней")
        self.assertEqual(util.days_phrase(21), "через 21 день")
        util.set_now(None)


class RatesTests(unittest.TestCase):
    def setUp(self):
        util.set_now(datetime(2026, 10, 9, 12, 0))
        self.db = dbm.DB(":memory:")

    def tearDown(self):
        util.set_now(None)

    def test_parse_formats(self):
        import json
        j = rt.parse_json_rates(json.dumps({"Valute": {"KZT": {"Nominal": 100, "Value": 19.5}}}).encode())
        self.assertAlmostEqual(j["KZT"], 0.195)
        x = rt.parse_xml_rates('<?xml version="1.0" encoding="windows-1251"?><ValCurs><Valute><CharCode>USD</CharCode>'
                               '<Nominal>1</Nominal><Value>95,5000</Value></Valute></ValCurs>'.encode("cp1251"))
        self.assertAlmostEqual(x["USD"], 95.5)

    def test_manual_overrides_auto(self):
        online = {"ok": False}

        def fetcher():
            if not online["ok"]:
                raise RuntimeError("нет сети")
            return {"USD": 95.0}

        r = rt.Rates(self.db, fetcher=fetcher)
        self.assertIsNone(r.get("USD"))
        with self.assertRaises(rt.NoRate):
            r.to_base(10, "USD")
        online["ok"] = True
        with self.assertRaises(rt.NoRate):          # повторная попытка не чаще раза в 10 минут
            r.to_base(10, "USD")
        util.set_now(datetime(2026, 10, 9, 12, 11))
        self.assertEqual(r.to_base(10, "USD")[0], 950)   # сам подтянул курс, когда понадобился
        r.set_manual("USD", 100)
        self.assertEqual(r.to_base(10, "USD")[0], 1000)
        r.clear_manual("USD")
        self.assertEqual(r.get("USD"), 95.0)
        self.assertEqual(r.convert(190, "RUB", "USD"), 2.0)


class QuickTests(unittest.TestCase):
    def setUp(self):
        self.db = dbm.DB(":memory:")

    def test_routes(self):
        r = quick.route
        self.assertEqual(r("Баланс?")[0]["what"], "balance")
        self.assertEqual(r("/balance")[0]["what"], "balance")
        self.assertEqual(r("статистика за неделю")[0], {"type": "query", "what": "stats", "period": "week"})
        self.assertEqual(r("статистика за прошлую неделю")[0]["period"], "last_week")
        self.assertEqual(r("траты")[0]["period"], "month")
        self.assertEqual(r("задачи")[0]["what"], "tasks")
        self.assertEqual(r("отмени")[0]["type"], "undo")
        self.assertIsNone(r("отмена"), "«отмена» не должна откатывать записи")
        self.assertEqual(r("верни #12")[0], {"type": "restore", "entity": "tx", "id": 12})
        self.assertEqual(r("утро выкл")[0], {"type": "settings", "key": "morning", "value": "off"})
        self.assertEqual(r("итоги недели вкл")[0]["value"], "on")
        self.assertEqual(r("итоги недели")[0]["what"], "weekly")
        self.assertEqual(r("курс доллара 95")[0], {"type": "rate", "currency": "USD", "value": "95"})
        self.assertEqual(r("курс евро авто")[0]["value"], "авто")
        self.assertEqual(r("бэкап")[0]["type"], "backup")
        self.assertIsNone(r("потратил 300 на еду"))
        self.assertIsNone(r("статистика по еде"))

    def test_simple_tx_needs_known_category(self):
        s = quick.simple_tx
        self.assertEqual(s("300 шаурма", self.db)[0]["category"], "Еда")
        self.assertEqual(s("шаурма 300", self.db)[0]["amount"], 300)
        self.assertEqual(s("потратил 150 на метро", self.db)[0]["category"], "Транспорт")
        self.assertEqual(s("такси 1 200", self.db)[0]["amount"], 1200)
        a = s("+50к зарплата", self.db)[0]
        self.assertEqual((a["type"], a["amount"], a["category"]), ("income", 50000, "Зарплата"))
        a = s("пришла зарплата 50000", self.db)[0]
        self.assertEqual(a["type"], "income")
        self.assertEqual(s("15 евро кофе", self.db)[0]["currency"], "EUR")
        self.assertIsNone(s("300 странная штука", self.db), "неизвестное слово -> отдать ИИ")
        self.assertEqual(s("300 странная штука", self.db, allow_unknown=True)[0]["category"], "Другое")
        self.assertIsNone(s("2 кофе", self.db), "2 кофе — это не 2 рубля")
        self.assertIsNone(s("напомни 10 минут", self.db))
        self.assertIsNone(s("кофе 300 и метро 150", self.db))
        self.assertIsNone(s("привет как дела", self.db))

    def test_memory_beats_dictionary(self):
        self.db.insert("tx", kind="expense", amount=500, currency="RUB", orig_amount=500, category="Здоровье",
                       description="витамины", date="2026-10-01", created_at="x")
        self.assertEqual(quick.simple_tx("витамины 700", self.db)[0]["category"], "Здоровье")


class LLMParsingTests(unittest.TestCase):
    def test_json_extraction(self):
        self.assertEqual(llm.parse_json_text('{"actions":[{"type":"undo"}]}'), {"actions": [{"type": "undo"}]})
        self.assertEqual(llm.parse_json_text('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(llm.parse_json_text('Вот: {"a":1} готово'), {"a": 1})
        self.assertIsNone(llm.parse_json_text("не json"))
        self.assertEqual(llm.to_actions({"type": "undo"}), [{"type": "undo"}])
        self.assertEqual(llm.to_actions([{"type": "a"}, "x"]), [{"type": "a"}])
        self.assertEqual(llm.to_actions({"actions": "bad"}), [])

    def test_prompt_has_real_dates(self):
        s = llm.build_system(date(2026, 10, 9))
        self.assertIn("2026-10-10 10:00", s)
        self.assertIn("2027-03-01", s)


if __name__ == "__main__":
    unittest.main()
