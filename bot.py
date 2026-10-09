#!/usr/bin/env python3
"""Личный помощник в Telegram: деньги, дела, долги, заначки, подписки, дневник.

Запуск:  python3 bot.py     (настройки — в файле .env рядом, см. README.md)
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
import time

import config
import net
import quick
import util
from db import DB
from engine import Engine, Reply
from jobs import Jobs
from llm import LLM, LLMUnavailable
from rates import Rates
from tg import TG, TGError
from util import esc

log = logging.getLogger("bot")

COMMANDS = [("balance", "Баланс"), ("stats", "Статистика"), ("tasks", "Задачи"), ("debts", "Долги"),
            ("stashes", "Заначки и цели"), ("subs", "Подписки и сроки"), ("recent", "Последние записи"),
            ("undo", "Отменить последнее"), ("backup", "Бэкап базы"), ("help", "Что я умею")]


class Bot:
    def __init__(self, tg, llm, db, rates, engine=None, owner_id: int | None = None, home_chat: int | None = None):
        self.tg, self.llm, self.db, self.rates = tg, llm, db, rates
        self.engine = engine or Engine(db, rates)
        self.owner_id = config.OWNER_ID if owner_id is None else owner_id
        self.home_chat = home_chat or config.HOME_CHAT_ID or self.owner_id
        self.hist: list[str] = []
        self.fails = 0
        off = db.setting("tg_offset")
        self.offset = int(off) if off else None
        self.jobs = Jobs(db, rates, self.engine, self.send_home, self.send_home_doc)
        self.llm.on_usage = self._usage

    # ------------------------------------------------------------ служебное
    def _usage(self, model: str, tokens: int) -> None:
        d = util.today().isoformat()
        for key, add in ((f"ai_tokens_{d}", tokens), (f"ai_requests_{d}", 1)):
            self.db.set_setting(key, int(self.db.setting(key, "0") or 0) + add)

    def send_home(self, x) -> None:
        if not self.home_chat:
            return
        if isinstance(x, str):
            self.tg.send(self.home_chat, x)
        else:
            self.deliver(self.home_chat, x)

    def send_home_doc(self, path: str, caption: str = "", chat: int | None = None) -> None:
        target = chat or self.home_chat
        if target:
            self.tg.send_document(target, path, caption)

    def deliver(self, chat: int, replies: list[Reply], thread: int | None = None) -> None:
        for r in replies:
            if r.kind == "backup":
                self.jobs.make_backup("💾 Бэкап базы. Сохрани файл — в нём все твои данные.", chat)
                continue
            if r.text:
                self.tg.send(chat, r.text, r.kb, thread)

    # ------------------------------------------------------------ сообщения
    def welcome(self, chat: int, thread: int | None) -> None:
        text = views_help()
        if self.db.setting("opening") is None:
            text += "\n\n👉 Начни с одного: напиши, сколько у тебя сейчас денег, например «сейчас у меня 12000»."
        if not self.llm.enabled:
            text += ("\n\n⚠️ Ключ GROQ_API_KEY не задан: работают команды и простые записи вида «300 шаурма», "
                     "но свободный текст и голос — нет.")
        self.tg.send(chat, text, thread_id=thread)

    def understand(self, text: str, chat: int) -> list[Reply]:
        pend = self.engine.resolve_pending(text)
        if pend is not None:
            return pend
        acts = quick.route(text)
        if acts:
            return self.engine.run(acts, text)
        acts = quick.simple_tx(text, self.db)
        if acts:
            return self.engine.run(acts, text)
        reason = ""
        if self.llm.enabled:
            self.tg.typing(chat)
            try:
                acts = self.llm.parse(text, self.engine.context(), self.hist)
            except LLMUnavailable as e:
                reason = str(e)
                log.warning("ИИ недоступен: %s", e)
            else:
                self.hist = (self.hist + [text])[-4:]
                replies = self.engine.run(acts, text)
                return replies or [Reply("Не понял 🤔 Скажи по-другому или напиши «помощь».")]
        acts = quick.simple_tx(text, self.db, allow_unknown=True)
        if acts:
            replies = self.engine.run(acts, text)
            if replies:
                replies[0].text += "\n<i>ИИ сейчас недоступен — категорию подобрал сам, поправь ✏️, если не так.</i>"
            return replies
        why = f" ({esc(reason)})" if reason else ""
        return [Reply("🤖 ИИ сейчас недоступен" + why + ". Команды («баланс», «задачи», «подписки») и простые записи "
                      "вроде «300 шаурма» работают без него. Остальное — попробуй чуть позже.")]

    def on_text(self, chat: int, text: str, thread: int | None) -> None:
        if text.strip().casefold().startswith("/start"):
            return self.welcome(chat, thread)
        self.deliver(chat, self.understand(text, chat), thread)

    def handle_message(self, m: dict) -> None:
        chat = m["chat"]["id"]
        frm = (m.get("from") or {}).get("id")
        thread = m.get("message_thread_id") if m.get("is_topic_message") else None
        if not self.owner_id:
            if m["chat"].get("type") == "private":
                self.tg.send(chat, f"Привет! Твой Telegram ID: <code>{frm}</code>\n"
                                   f"Впиши в файл .env строку OWNER_ID={frm} и перезапусти бота — "
                                   f"после этого он будет отвечать только тебе.")
            return
        if frm != self.owner_id:
            log.info("сообщение от чужого пользователя %s проигнорировано", frm)
            return
        voice = m.get("voice") or m.get("audio")
        if voice:
            return self.on_voice(chat, voice, thread)
        text = m.get("text") or m.get("caption")
        if text:
            return self.on_text(chat, text, thread)
        self.tg.send(chat, "Пока понимаю только текст и голосовые. Фото и файлы не разбираю — опиши словами.",
                     thread_id=thread)

    def on_voice(self, chat: int, v: dict, thread: int | None) -> None:
        if not self.llm.enabled:
            self.tg.send(chat, "Для голосовых нужен ключ GROQ_API_KEY.", thread_id=thread)
            return
        if (v.get("duration") or 0) > 180:
            self.tg.send(chat, "Голосовое длиннее 3 минут — запиши покороче.", thread_id=thread)
            return
        self.tg.typing(chat)
        try:
            audio = self.tg.download(v["file_id"])
            text = self.llm.transcribe(audio, "voice.ogg")
        except (LLMUnavailable, TGError, net.HTTPError, OSError) as e:
            self.tg.send(chat, f"Не получилось распознать голос: {esc(e)}", thread_id=thread)
            return
        if not text:
            self.tg.send(chat, "Ничего не расслышал 🙈 Попробуй ещё раз.", thread_id=thread)
            return
        self.tg.send(chat, f"🎙 <i>{esc(text)}</i>", thread_id=thread)
        self.on_text(chat, text, thread)

    def handle_callback(self, cb: dict) -> None:
        frm = (cb.get("from") or {}).get("id")
        self.tg.answer_callback(cb["id"])
        if not self.owner_id or frm != self.owner_id:
            return
        msg = cb.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        mid = msg.get("message_id")
        thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None
        data = cb.get("data") or ""
        replies = self.engine.callback(data)
        if replies and replies[0].clear and chat and mid:
            if data.startswith(("tx:", "j:", "d:")):
                markup = (msg.get("reply_markup") or {}).get("inline_keyboard", [])
                rows = [row for row in markup if not any(b.get("callback_data") == data for b in row)]
            else:
                rows = []
            self.tg.set_markup(chat, mid, rows)
        if chat:
            self.deliver(chat, replies, thread)

    def handle_update(self, u: dict) -> None:
        if "callback_query" in u:
            return self.handle_callback(u["callback_query"])
        if u.get("message"):
            self.handle_message(u["message"])

    def safe_handle(self, u: dict) -> None:
        try:
            self.handle_update(u)
        except Exception:  # noqa: BLE001
            log.exception("ошибка при обработке обновления")
            chat = ((u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat") or {}).get("id")
            if chat and self.owner_id:
                try:
                    self.tg.send(chat, "⚠️ Что-то пошло не так. Ошибка записана в лог, твои данные целы.")
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------ главный цикл
    def poll_once(self, timeout: int = 25) -> int:
        updates = self.tg.get_updates(self.offset, timeout)
        for u in updates:
            self.safe_handle(u)
            self.offset = u["update_id"] + 1
            self.db.set_setting("tg_offset", self.offset)
        return len(updates)

    def run(self) -> None:
        self.tg.set_commands(COMMANDS)
        log.info("бот запущен; владелец=%s; ИИ=%s", self.owner_id or "не задан", "да" if self.llm.enabled else "нет")
        try:
            self.jobs.rates_job(util.now())   # курсы нужны с первой минуты
        except Exception:  # noqa: BLE001
            log.exception("стартовое обновление курсов")
        while True:
            try:
                self.poll_once()
                self.fails = 0
            except (net.HTTPError, TGError, OSError, ValueError) as e:
                if isinstance(e, net.HTTPError) and e.status == 409:
                    log.error("409: бот уже запущен в другом месте — останови второй экземпляр")
                else:
                    log.warning("связь с Telegram: %s", e)
                self.fails += 1
                time.sleep(min(60, 2 ** min(self.fails, 6)))
            try:
                self.jobs.tick()
            except Exception:  # noqa: BLE001
                log.exception("ошибка в расписании")


def views_help() -> str:
    import views
    return views.help_text()


def setup_logging() -> None:
    from pathlib import Path
    Path(config.LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout),
              logging.handlers.RotatingFileHandler(config.LOG_PATH, maxBytes=1_000_000, backupCount=3,
                                                   encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)


def main() -> None:
    setup_logging()
    if not config.TELEGRAM_TOKEN:
        print("Не задан TELEGRAM_TOKEN. Скопируй .env.example в .env и заполни (см. README.md).")
        sys.exit(1)
    db = DB(config.DB_PATH)
    rates = Rates(db)
    bot = Bot(TG(config.TELEGRAM_TOKEN), LLM(), db, rates)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("остановлен")


if __name__ == "__main__":
    main()
