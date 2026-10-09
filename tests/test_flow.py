import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.mocks import OWNER, Chat, html_problems, make_env  # noqa: E402
import util  # noqa: E402


def texts(msgs):
    return "\n".join(m["text"] for m in msgs)


class FlowBase(unittest.TestCase):
    with_llm = True

    def setUp(self):
        util.set_now(datetime(2026, 10, 9, 20, 30))   # пятница
        self.bot, self.srv, self.tmp = make_env(with_llm=self.with_llm)
        self.st = self.srv.state
        self.chat = Chat(self.bot, self.srv)

    def tearDown(self):
        util.set_now(None)
        bad = [(m["text"][:60], html_problems(m["text"])) for m in self.st.sent
               if m.get("parse_mode") == "HTML" and html_problems(m["text"])]
        self.srv.close()
        self.bot.db.conn.close()
        self.assertEqual(bad, [], "в сообщениях сломанная HTML-разметка Telegram")

    def set_time(self, s):
        util.set_now(datetime.fromisoformat(s))


class AccessTests(FlowBase):
    def test_stranger_is_ignored(self):
        self.assertEqual(self.chat.say("баланс", user=555), [])
        self.assertEqual(self.chat.press("tx:d:1", user=555), [])
        self.assertEqual(self.bot.db.one("SELECT COUNT(*) c FROM tx")["c"], 0)

    def test_owner_discovery_mode(self):
        self.bot.owner_id = 0
        out = self.chat.say("привет", user=4242)
        self.assertIn("OWNER_ID=4242", texts(out))
        self.bot.db.set_setting("x", "1")
        out = self.chat.say("300 шаурма", user=4242)
        self.assertEqual(self.bot.db.one("SELECT COUNT(*) c FROM tx")["c"], 0)

    def test_start_and_help(self):
        out = self.chat.say("/start")
        self.assertIn("сейчас у меня", texts(out))
        self.assertEqual(len(self.st.llm_calls), 0)

    def test_photo_gets_polite_reply(self):
        self.assertIn("не разбираю", texts(self.chat.photo()))


class QuickPathTests(FlowBase):
    def test_simple_expense_uses_no_ai(self):
        out = self.chat.say("300 шаурма")
        self.assertIn("Еда", texts(out))
        self.assertEqual(len(self.st.llm_calls), 0, "простая запись не должна тратить лимит ИИ")
        self.assertEqual(self.chat.buttons(out[0]), ["tx:e:1", "tx:d:1"])
        self.assertEqual(out[0]["parse_mode"], "HTML")

    def test_commands_use_no_ai(self):
        for cmd in ("баланс", "/balance", "задачи", "подписки", "долги", "заначки", "статистика", "последние",
                    "что я менял", "категории", "курсы", "настройки", "помощь", "утро", "итоги недели"):
            out = self.chat.say(cmd)
            self.assertTrue(out and out[0]["text"], cmd)
        self.assertEqual(len(self.st.llm_calls), 0)


class AIPathTests(FlowBase):
    def test_multi_item_message_is_one_reply(self):
        self.st.llm_script = lambda t: [
            {"type": "expense", "amount": 3500, "category": "Одежда", "description": "кроссовки"},
            {"type": "expense", "amount": 200, "category": "Еда", "description": "кофе"}]
        out = self.chat.say("купил кроссовки за 3500 и ещё кофе 200")
        self.assertEqual(len(out), 1)
        self.assertEqual(self.chat.buttons(out[0]), ["tx:e:1", "tx:d:1", "tx:e:2", "tx:d:2"])
        sent = self.st.llm_calls[-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("2026-10-10 сб", sent[1]["content"])        # календарь
        self.assertIn("Одежда", sent[1]["content"])               # список категорий
        self.assertEqual(self.st.llm_calls[-1]["response_format"], {"type": "json_object"})
        self.assertEqual(self.st.llm_calls[-1]["model"], "llama-3.3-70b-versatile")
        self.assertEqual(self.bot.db.setting("ai_requests_2026-10-09"), "1")
        self.assertEqual(self.bot.db.setting("ai_tokens_2026-10-09"), "1500")

    def test_full_day_story(self):
        script = {
            "сейчас у меня 12000": [{"type": "set_balance", "amount": 12000}],
            "купил игру за 15 евро": [{"type": "expense", "amount": 15, "currency": "EUR",
                                       "category": "Развлечения", "description": "игра"}],
            "Claude Pro 20 долларов каждое 14-е": [{"type": "sub", "name": "Claude Pro", "amount": 20,
                                                    "currency": "USD", "day": 14, "category": "Подписки"}],
            "напомни завтра в 10 сдать отчёт": [{"type": "task", "text": "сдать отчёт", "due": "2026-10-10 10:00"}],
            "одолжил Васе пятихатку": [{"type": "debt", "person": "Вася", "direction": "owed_to_me", "amount": 500}],
            "хочу накопить 60к на ноутбук до марта": [{"type": "stash", "name": "Ноутбук", "target": 60000,
                                                       "deadline": "2027-03-01"}],
            "отложил 2000": [{"type": "stash_move", "amount": 2000, "direction": "in"}],
            "сколько я потратил на развлечения": [{"type": "query", "what": "stats", "category": "Развлечения"}],
        }
        self.st.llm_script = lambda t: script[t]
        for t in script:
            out = self.chat.say(t)
            self.assertTrue(out, t)
        # 12000 − 15€(1545) − 500 долг − 2000 в копилку
        import calc
        self.assertEqual(calc.balance(self.bot.db), 12000 - 1545 - 500 - 2000)
        out = self.chat.say("баланс")
        self.assertIn("7 955", texts(out).replace(" ", " "))

    def test_edit_by_words_uses_last(self):
        self.st.llm_script = lambda t: [{"type": "edit_tx", "id": "last", "category": "Транспорт"}]
        self.chat.say("300 шаурма")
        out = self.chat.say("это не еда а транспорт")
        self.assertIn("Исправил", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["category"], "Транспорт")

    def test_unclear_message_gets_clarifying_question(self):
        self.st.llm_script = lambda t: [{"type": "say", "text": "Что именно записать?"}]
        self.assertIn("Что именно записать?", texts(self.chat.say("ну это самое")))

    def test_empty_actions(self):
        self.st.llm_script = lambda t: []
        self.assertIn("Не понял", texts(self.chat.say("абракадабра")))

    def test_garbage_from_model(self):
        self.st.llm_raw = "это не json"
        out = texts(self.chat.say("что-то сложное"))
        self.assertIn("ИИ сейчас недоступен", out)
        self.st.llm_raw = json.dumps({"actions": [{"type": "expense", "amount": "много"}]})
        self.bot.llm.cooldown.clear()
        self.assertIn("Не понял сумму", texts(self.chat.say("что-то сложное 2")))


class LimitsTests(FlowBase):
    def test_falls_back_to_second_model_then_skips_first(self):
        self.st.model_status["llama-3.3-70b-versatile"] = 429
        self.st.llm_script = lambda t: [{"type": "expense", "amount": 300, "category": "Другое"}]
        out = self.chat.say("странная штука 300")
        self.assertIn("300", texts(out))
        self.assertEqual([c["model"] for c in self.st.llm_calls], ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"])
        self.chat.say("странная штука 400")
        self.assertEqual([c["model"] for c in self.st.llm_calls][2:], ["llama-3.1-8b-instant"],
                         "большая модель на паузе — не долбим её зря")

    def test_all_models_limited_still_records_simple_things(self):
        self.st.model_status.update({"llama-3.3-70b-versatile": 429, "llama-3.1-8b-instant": 429})
        out = self.chat.say("300 странная штука")
        self.assertIn("ИИ сейчас недоступен", texts(out))
        self.assertIn("Другое", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["amount"], 300)
        out = self.chat.say("расскажи анекдот")
        self.assertIn("ИИ сейчас недоступен", texts(out))
        self.assertIn("баланс", texts(self.chat.say("баланс")).lower())

    def test_bad_key(self):
        self.st.model_status.update({"llama-3.3-70b-versatile": 401, "llama-3.1-8b-instant": 401})
        self.assertIn("не принял ключ", texts(self.chat.say("расскажи анекдот")))


class NoKeyTests(FlowBase):
    with_llm = False

    def test_works_without_groq_key(self):
        self.assertIn("GROQ_API_KEY", texts(self.chat.say("/start")))
        self.assertIn("Еда", texts(self.chat.say("300 шаурма")))
        self.assertIn("ИИ сейчас недоступен", texts(self.chat.say("расскажи анекдот")))
        self.assertIn("GROQ_API_KEY", texts(self.chat.voice()))


class VoiceTests(FlowBase):
    def test_voice_to_expense(self):
        self.st.transcript = "триста на шаурму и сто пятьдесят метро"
        self.st.llm_script = lambda t: [
            {"type": "expense", "amount": 300, "category": "Еда", "description": "шаурма"},
            {"type": "expense", "amount": 150, "category": "Транспорт", "description": "метро"}]
        out = self.chat.voice()
        self.assertEqual(len(out), 2)
        self.assertIn("🎙", out[0]["text"])
        self.assertIn("триста на шаурму", out[0]["text"])
        self.assertEqual(self.bot.db.one("SELECT SUM(amount) s FROM tx")["s"], 450)

    def test_voice_errors(self):
        self.st.stt_status = 429
        self.assertIn("лимит распознавания", texts(self.chat.voice()))
        self.st.stt_status = 200
        self.st.transcript = ""
        self.assertIn("Ничего не расслышал", texts(self.chat.voice()))
        self.assertIn("длиннее 3 минут", texts(self.chat.voice(duration=500)))


class ButtonTests(FlowBase):
    def test_edit_amount_via_buttons(self):
        out = self.chat.say("300 шаурма")
        out = self.chat.press("tx:e:1")
        self.assertEqual(self.chat.buttons(out[0])[:2], ["tx:f:1:amount", "tx:f:1:category"])
        out = self.chat.press("tx:f:1:amount")
        self.assertIn("новую сумму", out[0]["text"])
        out = self.chat.say("450")
        self.assertIn("Исправил", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["amount"], 450)
        self.assertEqual(len(self.st.llm_calls), 0)

    def test_edit_category_via_buttons(self):
        self.chat.say("300 шаурма")
        out = self.chat.press("tx:f:1:category")
        cbs = self.chat.buttons(out[0])
        idx = self.bot.db.category_names().index("Транспорт")
        self.assertIn(f"tx:c:1:{idx}", cbs)
        self.chat.press(f"tx:c:1:{idx}")
        self.assertEqual(self.bot.db.get("tx", 1)["category"], "Транспорт")
        self.chat.press("tx:f:1:newcat")
        self.chat.say("Питомцы")
        self.assertEqual(self.bot.db.get("tx", 1)["category"], "Питомцы")

    def test_edit_date_foreign_and_cancel(self):
        self.chat.say("300 шаурма")
        self.chat.press("tx:f:1:date")
        self.chat.say("05.10")
        self.assertEqual(self.bot.db.get("tx", 1)["date"], "2026-10-05")
        self.chat.press("tx:f:1:amount")
        self.chat.say("10 евро")
        self.assertEqual(self.bot.db.get("tx", 1)["currency"], "EUR")
        self.chat.press("tx:f:1:amount")
        self.assertIn("не меняю", texts(self.chat.say("отмена")))
        self.assertEqual(self.bot.db.get("tx", 1)["orig_amount"], 10)

    def test_pending_does_not_trap_user(self):
        self.chat.say("300 шаурма")
        self.chat.press("tx:f:1:amount")
        out = self.chat.say("баланс")            # не число — обычная команда
        self.assertIn("Баланс", texts(out))
        self.assertIsNone(self.bot.engine.pending)

    def test_delete_and_restore_buttons(self):
        msg = self.chat.say("300 шаурма")[0]
        markup = {"inline_keyboard": [[{"text": "a", "callback_data": "tx:e:1"}, {"text": "b", "callback_data": "tx:d:1"}]]}
        out = self.chat.press("tx:d:1", {"message_id": msg["message_id"], "chat": {"id": OWNER},
                                         "reply_markup": markup})
        self.assertIn("Удалил", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["deleted"], 1)
        self.assertEqual(self.st.edits[-1]["reply_markup"], {"inline_keyboard": []})
        self.assertEqual(self.chat.buttons(out[0]), ["r:tx:1"])
        out = self.chat.press("r:tx:1")
        self.assertIn("Вернул", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["deleted"], 0)
        # повторное нажатие старой кнопки безопасно
        self.assertIn("на месте", texts(self.chat.press("r:tx:1")))
        self.assertIn("устарела", texts(self.chat.press("zzz:1:2")))
        self.assertTrue(self.st.answered)

    def test_only_pressed_row_is_removed_in_merged_message(self):
        self.st.llm_script = lambda t: [{"type": "expense", "amount": 300, "category": "Еда"},
                                        {"type": "expense", "amount": 150, "category": "Транспорт"}]
        msg = self.chat.say("кофе и такси чтобы пройти ИИ")[0]
        self.assertEqual(len(self.chat.buttons(msg)), 4)
        self.chat.press("tx:d:2", {"message_id": msg["message_id"], "chat": {"id": OWNER},
                                   "reply_markup": msg["reply_markup"]})
        left = [b["callback_data"] for row in self.st.edits[-1]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(left, ["tx:e:1", "tx:d:1"])


class TelegramQuirksTests(FlowBase):
    def test_html_parse_error_falls_back_to_plain(self):
        self.st.parse_fail_once = True
        out = self.chat.say("баланс")
        self.assertTrue(out)
        self.assertNotIn("parse_mode", out[0])
        self.assertNotIn("<b>", out[0]["text"])

    def test_offset_survives_restart(self):
        self.chat.say("300 шаурма")
        self.chat.say("200 кофе")
        self.assertEqual(self.bot.db.setting("tg_offset"), "3")
        from bot import Bot
        from llm import LLM
        from tg import TG
        from tests.mocks import TOKEN
        b2 = Bot(TG(TOKEN, self.srv.url), LLM(key="k", base=self.srv.url + "/openai/v1"), self.bot.db,
                 self.bot.rates, owner_id=OWNER)
        self.assertEqual(b2.offset, 3)

    def test_poison_message_does_not_kill_bot(self):
        self.bot.engine.run = lambda *a, **k: 1 / 0
        out = self.chat.say("300 шаурма")
        self.assertIn("Что-то пошло не так", texts(out))
        self.assertEqual(self.bot.offset, 2)

    def test_long_message_is_split(self):
        from tg import chunks
        text = "\n".join(f"строка {i} " + "я" * 100 for i in range(80))
        parts = chunks(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        self.assertEqual("\n".join(parts), text)


class SchedulerTests(FlowBase):
    def quiet(self):
        self.bot.db.set_setting("morning", "off")

    def test_task_reminder_and_snooze(self):
        self.quiet()
        self.st.llm_script = lambda t: [{"type": "task", "text": "сдать отчёт", "due": "2026-10-10 10:00"}]
        out = self.chat.say("напомни завтра в 10 сдать отчёт")
        self.assertIn("Напомню", texts(out))
        self.set_time("2026-10-10 09:59")
        self.assertEqual(self.chat.tick(), [])
        self.set_time("2026-10-10 10:00")
        out = self.chat.tick()
        self.assertEqual(len(out), 1)
        self.assertIn("сдать отчёт", out[0]["text"])
        self.assertEqual(self.chat.buttons(out[0]), ["t:done:1", "t:snz:1", "t:tom:1", "t:del:1"])
        self.assertEqual(self.chat.tick(), [], "второй раз не напоминает")
        self.chat.press("t:snz:1")
        self.set_time("2026-10-10 11:00")
        self.assertEqual(len(self.chat.tick()), 1)
        self.chat.press("t:done:1")
        self.set_time("2026-10-10 15:00")
        self.assertEqual(self.chat.tick(), [])

    def test_reminder_after_downtime_says_it_is_late(self):
        self.bot.engine.run([{"type": "task", "text": "позвонить маме", "due": "2026-10-10 10:00"}])
        self.set_time("2026-10-10 18:00")
        out = self.chat.tick()
        self.assertIn("должно было прийти", out[0]["text"])

    def test_subscription_cycle(self):
        self.quiet()
        self.bot.engine.run([{"type": "set_balance", "amount": 200},
                             {"type": "sub", "name": "Сервер", "amount": 300, "next_due": "2026-10-12"}])
        self.set_time("2026-10-09 08:59")
        self.assertEqual(self.chat.tick(), [], "до утреннего часа молчит")
        self.set_time("2026-10-09 09:05")
        out = self.chat.tick()
        self.assertIn("Через 3 дня", out[0]["text"])
        self.assertIn("не хватает 100", out[0]["text"].replace(" ", " "))
        self.assertEqual(self.chat.tick(), [])
        self.set_time("2026-10-11 09:05")
        self.assertIn("Завтра", self.chat.tick()[0]["text"])
        self.set_time("2026-10-12 09:05")
        out = self.chat.tick()
        self.assertEqual(self.chat.buttons(out[0])[:2], ["rp:ok:1", "rp:oth:1"])
        self.assertEqual(self.chat.tick(), [])
        out = self.chat.press("rp:ok:1")
        self.assertIn("Записал расход", texts(out))
        self.assertIn("12 ноя", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["amount"], 300)
        self.set_time("2026-10-13 09:05")
        self.assertEqual(self.chat.tick(), [])

    def test_unanswered_payment_nags_five_times_then_stops(self):
        self.quiet()
        self.bot.engine.run([{"type": "sub", "name": "Интернет", "amount": 500, "next_due": "2026-10-10"}])
        counts = []
        for day in range(10, 20):
            self.set_time(f"2026-10-{day} 09:05")
            counts.append(len(self.chat.tick()))
        self.assertEqual(counts, [1, 1, 1, 1, 1, 1, 0, 0, 0, 0])   # 5 вопросов + 1 прощальное
        self.set_time("2026-10-20 09:05")
        self.assertEqual(self.chat.tick(), [])

    def test_custom_amount_flow_and_income_prompt(self):
        self.quiet()
        self.bot.engine.run([{"type": "sub", "name": "Зарплата", "kind": "income", "amount": 40000,
                              "next_due": "2026-10-10"}])
        self.set_time("2026-10-10 09:05")
        out = self.chat.tick()
        self.assertIn("Пришло?", out[0]["text"])
        self.chat.press("rp:oth:1")
        out = self.chat.say("42 500")
        self.assertIn("Записал доход", texts(out))
        self.assertEqual(self.bot.db.get("tx", 1)["amount"], 42500)
        self.assertEqual(self.bot.db.get("recurring", 1)["next_due"], "2026-11-10")

    def test_expiry_prompt(self):
        self.quiet()
        self.bot.engine.run([{"type": "sub", "name": "Домен", "amount": 0, "next_due": "2026-10-10"}])
        self.set_time("2026-10-10 09:05")
        out = self.chat.tick()
        self.assertEqual(self.chat.buttons(out[0]), ["rp:ext_m:1", "rp:ext_y:1", "rp:off:1"])
        self.chat.press("rp:ext_y:1")
        self.assertEqual(self.bot.db.get("recurring", 1)["next_due"], "2027-10-10")

    def test_morning_brief_once_and_can_be_disabled(self):
        self.bot.engine.run([{"type": "task", "text": "сдать отчёт", "due": "2026-10-10"},
                             {"type": "sub", "name": "Зарплата", "kind": "income", "amount": 50000,
                              "next_due": "2026-10-15"}])
        self.set_time("2026-10-10 09:00")
        self.chat.tick()   # курсы и т.п.
        # утренний вызов уже мог пройти в первом tick — смотрим всю историю
        msgs = [m["text"] for m in self.st.sent]
        brief = [m for m in msgs if "Доброе утро" in m]
        self.assertEqual(len(brief), 1)
        self.assertIn("сдать отчёт", brief[0])
        self.chat.tick()
        self.assertEqual(len([m for m in self.st.sent if "Доброе утро" in m["text"]]), 1)
        self.chat.say("утро выкл")
        self.set_time("2026-10-11 09:00")
        self.chat.tick()
        self.assertEqual(len([m for m in self.st.sent if "Доброе утро" in m["text"]]), 1)

    def test_no_morning_brief_when_started_late_or_empty(self):
        self.set_time("2026-10-10 09:00")
        self.chat.tick()
        self.assertEqual([m for m in self.st.sent if "Доброе утро" in m["text"]], [], "пустая база — не шумим")
        self.bot.engine.run([{"type": "task", "text": "x", "due": "2026-10-11"}])
        self.set_time("2026-10-11 22:00")
        self.chat.tick()
        self.assertEqual([m for m in self.st.sent if "Доброе утро" in m["text"]], [], "вечером «доброе утро» не нужно")

    def test_weekly_review_and_backup_on_sunday(self):
        self.bot.engine.run([{"type": "expense", "amount": 3000, "category": "Еда", "date": "2026-10-06"},
                             {"type": "expense", "amount": 500, "category": "Еда", "date": "2026-09-30"},
                             {"type": "journal", "kind": "workout", "text": "жим", "date": "2026-10-08"}])
        self.set_time("2026-10-11 19:59")   # воскресенье
        self.chat.tick()
        self.assertEqual([m for m in self.st.sent if "Итоги недели" in m["text"]], [])
        self.set_time("2026-10-11 20:00")
        self.chat.tick()
        weekly = [m["text"] for m in self.st.sent if "Итоги недели" in m["text"]]
        self.assertEqual(len(weekly), 1)
        self.assertIn("3 000", weekly[0].replace(" ", " "))
        self.assertIn("Тренировок: 1", weekly[0])
        self.assertEqual(self.st.docs, [])
        self.set_time("2026-10-11 21:00")
        self.chat.tick()
        self.assertEqual(len(self.st.docs), 1)
        self.assertTrue(self.st.docs[0]["has_sqlite"])
        self.assertEqual(self.st.docs[0]["chat_id"], OWNER)
        self.chat.tick()
        self.assertEqual(len(self.st.docs), 1, "бэкап раз в неделю")
        # ручной бэкап
        self.chat.say("бэкап")
        self.assertEqual(len(self.st.docs), 2)

    def test_rates_refresh_and_failure_backoff(self):
        self.set_time("2026-10-10 10:00")
        self.chat.tick()
        self.assertEqual(self.bot.rates.get("USD"), 95.0)
        self.assertAlmostEqual(self.bot.rates.get("KZT"), 0.195)
        self.assertIn("2026-10-10", self.bot.db.setting("rates_ok"))
        # на следующий день сервер лежит: не падаем и не долбим чаще, чем раз в 3 часа
        self.st.rates_down = True
        self.set_time("2026-10-11 10:00")
        self.chat.tick()
        calls_before = self.bot.db.setting("rates_try")
        self.set_time("2026-10-11 11:00")
        self.chat.tick()
        self.assertEqual(self.bot.db.setting("rates_try"), calls_before)
        self.assertEqual(self.bot.rates.get("USD"), 95.0, "остаются последние известные курсы")

    def test_xml_mirror_fallback(self):
        import config
        config.RATES_URL = self.srv.url + "/nope.js"
        self.set_time("2026-10-10 10:00")
        self.chat.tick()
        self.assertEqual(self.bot.rates.get("USD"), 95.5)


if __name__ == "__main__":
    unittest.main()
