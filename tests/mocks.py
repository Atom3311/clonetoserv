"""Поддельные Telegram, Groq и сайт с курсами — настоящие HTTP-серверы на localhost."""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TOKEN = "TESTTOKEN"
OWNER = 777

RATES_JSON = {"Valute": {"USD": {"Nominal": 1, "Value": 95.0}, "EUR": {"Nominal": 1, "Value": 103.0},
                         "KZT": {"Nominal": 100, "Value": 19.5}}}
RATES_XML = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="09.10.2026">'
             '<Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>95,5000</Value></Valute>'
             '<Valute><CharCode>KZT</CharCode><Nominal>100</Nominal><Value>19,5000</Value></Valute></ValCurs>')


class State:
    def __init__(self):
        self.updates: list[dict] = []
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.docs: list[dict] = []
        self.answered: list[str] = []
        self.llm_calls: list[dict] = []
        self.llm_script = None            # функция(текст) -> список действий
        self.llm_raw = None               # или готовая строка-ответ
        self.model_status: dict = {}      # модель -> HTTP-код ошибки
        self.transcript = "триста на шаурму"
        self.stt_status = 200
        self.uid = 0
        self.mid = 100
        self.parse_fail_once = False
        self.rates_down = False
        self.long_poll = False
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    state: State = None

    def log_message(self, *a):  # тишина
        pass

    def _send(self, obj=None, code=200, raw: bytes | None = None, headers: dict | None = None):
        body = raw if raw is not None else json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.state
        if self.path.startswith("/file/bot"):
            return self._send(raw=b"OggS-fake-audio")
        if self.path == "/rates.js":
            if st.rates_down:
                return self._send({"e": 1}, 503)
            return self._send(RATES_JSON)
        if self.path == "/rates.xml":
            if st.rates_down:
                return self._send({"e": 1}, 503)
            return self._send(raw=RATES_XML.encode("cp1251"))
        self._send({"e": "nf"}, 404)

    def do_POST(self):
        st = self.state
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        path = self.path
        if path.startswith(f"/bot{TOKEN}/"):
            method = path.split("/")[2]
            if method == "sendDocument":
                m = re.search(rb'filename="([^"]+)"', body)
                cid = re.search(rb'name="chat_id"\r\n\r\n(\d+)', body)
                with st.lock:
                    st.docs.append({"name": m.group(1).decode() if m else "", "size": len(body),
                                    "chat_id": int(cid.group(1)) if cid else None,
                                    "has_sqlite": b"SQLite format 3" in body})
                return self._send({"ok": True, "result": {"message_id": 1}})
            payload = json.loads(body or b"{}")
            if method == "getUpdates" and st.long_poll and not st.updates:
                import time
                time.sleep(0.3)
            with st.lock:
                if method == "getUpdates":
                    ups, st.updates = st.updates, []
                    if not ups and st.long_poll:
                        pass
                    else:
                        return self._send({"ok": True, "result": ups})
                if method == "sendMessage":
                    if st.parse_fail_once and payload.get("parse_mode"):
                        st.parse_fail_once = False
                        return self._send({"ok": False, "description": "Bad Request: can't parse entities"}, 400)
                    st.mid += 1
                    st.sent.append({**payload, "message_id": st.mid})
                    return self._send({"ok": True, "result": {"message_id": st.mid}})
                if method == "editMessageReplyMarkup":
                    st.edits.append(payload)
                    return self._send({"ok": True, "result": True})
                if method == "answerCallbackQuery":
                    st.answered.append(payload.get("callback_query_id"))
                    return self._send({"ok": True, "result": True})
                if method == "getFile":
                    return self._send({"ok": True, "result": {"file_path": "voice/file.oga", "file_size": 15}})
                return self._send({"ok": True, "result": True})
        if path == "/openai/v1/chat/completions":
            payload = json.loads(body)
            model = payload["model"]
            with st.lock:
                st.llm_calls.append(payload)
                code = st.model_status.get(model)
            if code:
                return self._send({"error": {"message": "Rate limit reached for model (TPD) per day"}}, code,
                                  headers={"retry-after": "120"} if code == 429 else None)
            user = payload["messages"][-1]["content"]
            text = user.split("MESSAGE: ", 1)[-1]
            if st.llm_raw is not None:
                content = st.llm_raw
            else:
                acts = st.llm_script(text) if st.llm_script else [{"type": "say", "text": "ок"}]
                content = json.dumps({"actions": acts}, ensure_ascii=False)
            return self._send({"choices": [{"message": {"content": content}}],
                               "usage": {"total_tokens": 1500}})
        if path == "/openai/v1/audio/transcriptions":
            if st.stt_status != 200:
                return self._send({"error": {"message": "x"}}, st.stt_status)
            return self._send({"text": st.transcript})
        self._send({"e": "nf"}, 404)


class Servers:
    def __init__(self):
        self.state = State()
        Handler.state = self.state
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def make_env(owner: int = OWNER, with_llm: bool = True):
    """Собрать бота против поддельных серверов. Возвращает (bot, servers, tmpdir)."""
    import config
    from db import DB
    from llm import LLM
    from rates import Rates
    from tg import TG
    from bot import Bot
    srv = Servers()
    config.RATES_URL = srv.url + "/rates.js"
    config.RATES_URL_2 = srv.url + "/rates.xml"
    tmp = tempfile.mkdtemp(prefix="lifebot_")
    db = DB(str(Path(tmp) / "life.db"))
    rates = Rates(db)
    bot = Bot(TG(TOKEN, srv.url), LLM(key="k" if with_llm else "", base=srv.url + "/openai/v1"), db, rates,
              owner_id=owner)
    return bot, srv, tmp


class Chat:
    """Удобная обёртка: написать боту и получить, что он ответил."""

    def __init__(self, bot, srv, user: int = OWNER):
        self.bot, self.srv, self.user = bot, srv, user
        self.seen = 0

    def _push(self, upd: dict):
        st = self.srv.state
        st.uid += 1
        st.updates.append({"update_id": st.uid, **upd})

    def _collect(self):
        new = self.srv.state.sent[self.seen:]
        self.seen = len(self.srv.state.sent)
        return new

    def say(self, text: str, user: int | None = None):
        u = user or self.user
        self._push({"message": {"message_id": 1, "from": {"id": u}, "chat": {"id": u, "type": "private"},
                                "text": text}})
        self.bot.poll_once(timeout=0)
        return self._collect()

    def voice(self, duration: int = 5):
        self._push({"message": {"message_id": 1, "from": {"id": self.user},
                                "chat": {"id": self.user, "type": "private"},
                                "voice": {"file_id": "F1", "duration": duration}}})
        self.bot.poll_once(timeout=0)
        return self._collect()

    def photo(self):
        self._push({"message": {"message_id": 1, "from": {"id": self.user},
                                "chat": {"id": self.user, "type": "private"}, "photo": [{"file_id": "p"}]}})
        self.bot.poll_once(timeout=0)
        return self._collect()

    def press(self, data: str, msg: dict | None = None, user: int | None = None):
        msg = msg or {"message_id": 5, "chat": {"id": self.user, "type": "private"}, "reply_markup": {
            "inline_keyboard": [[{"text": "x", "callback_data": data}]]}}
        self._push({"callback_query": {"id": "cb" + str(self.srv.state.uid), "from": {"id": user or self.user},
                                       "message": msg, "data": data}})
        self.bot.poll_once(timeout=0)
        return self._collect()

    def tick(self):
        self.bot.jobs.tick()
        return self._collect()

    @staticmethod
    def buttons(msg: dict) -> list[str]:
        mk = msg.get("reply_markup") or {}
        return [b["callback_data"] for row in mk.get("inline_keyboard", []) for b in row]


ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a"}


def html_problems(text: str) -> list[str]:
    """Telegram отвергает сообщение, если теги не закрыты или есть «голые» < > &."""
    problems, stack = [], []
    pos = 0
    for m in re.finditer(r"<(/?)([a-zA-Z]+)[^<>]*>", text):
        problems += _bare(text[pos:m.start()])
        pos = m.end()
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag not in ALLOWED_TAGS:
            problems.append(f"тег <{tag}> не поддерживается")
        elif closing:
            if not stack or stack.pop() != tag:
                problems.append(f"лишний закрывающий </{tag}>")
        else:
            stack.append(tag)
    problems += _bare(text[pos:])
    if stack:
        problems.append(f"не закрыты теги: {stack}")
    return problems


def _bare(chunk: str) -> list[str]:
    out = []
    if "<" in chunk or ">" in chunk:
        out.append(f"голые < или > в {chunk!r}")
    if re.search(r"&(?!(amp|lt|gt|quot|#\d+);)", chunk):
        out.append(f"голый & в {chunk!r}")
    return out
