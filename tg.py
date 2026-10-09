"""Telegram Bot API на стандартной библиотеке (long polling)."""
from __future__ import annotations

import json
import re

import config
import net


class TGError(Exception):
    pass


def strip_tags(s: str) -> str:
    import html
    return html.unescape(re.sub(r"</?[a-z][^>]*>", "", s))


def chunks(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out


def keyboard(kb) -> dict | None:
    if not kb:
        return None
    rows = []
    for row in kb:
        r = []
        for label, data in row:
            if len(data.encode("utf-8")) > 64:
                continue
            r.append({"text": label, "callback_data": data})
        if r:
            rows.append(r)
    return {"inline_keyboard": rows} if rows else None


class TG:
    def __init__(self, token: str, base: str | None = None):
        self.token = token
        self.base = (base or config.TG_BASE).rstrip("/")

    def call(self, method: str, payload: dict | None = None, timeout: float = 30):
        url = f"{self.base}/bot{self.token}/{method}"
        data = net.post_json(url, payload or {}, timeout=timeout)
        if not data.get("ok"):
            raise TGError(str(data))
        return data["result"]

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 15)

    def send(self, chat_id: int, text: str, kb=None, thread_id: int | None = None) -> list[int]:
        ids = []
        parts = chunks(text or "…")
        for i, part in enumerate(parts):
            payload = {"chat_id": chat_id, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}
            if thread_id:
                payload["message_thread_id"] = thread_id
            markup = keyboard(kb) if i == len(parts) - 1 else None
            if markup:
                payload["reply_markup"] = markup
            try:
                res = self.call("sendMessage", payload)
            except net.HTTPError as e:
                if e.status == 400 and "parse" in e.text().casefold():
                    payload.pop("parse_mode")
                    payload["text"] = strip_tags(part)
                    res = self.call("sendMessage", payload)
                else:
                    raise
            ids.append(res.get("message_id"))
        return ids

    def set_markup(self, chat_id: int, message_id: int, rows: list) -> None:
        try:
            self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                 "reply_markup": {"inline_keyboard": rows}})
        except (net.HTTPError, TGError):
            pass  # сообщение могло быть уже изменено — не страшно

    def answer_callback(self, cb_id: str, text: str | None = None) -> None:
        payload = {"callback_query_id": cb_id}
        if text:
            payload["text"] = text
        try:
            self.call("answerCallbackQuery", payload)
        except (net.HTTPError, TGError):
            pass

    def typing(self, chat_id: int) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=10)
        except Exception:  # noqa: BLE001
            pass

    def download(self, file_id: str, max_bytes: int = 20_000_000) -> bytes:
        info = self.call("getFile", {"file_id": file_id})
        if info.get("file_size") and info["file_size"] > max_bytes:
            raise TGError("файл слишком большой")
        url = f"{self.base}/file/bot{self.token}/{info['file_path']}"
        _, body, _ = net.request("GET", url, timeout=60)
        return body

    def send_document(self, chat_id: int, path: str, caption: str = "") -> None:
        with open(path, "rb") as f:
            content = f.read()
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        body, ctype = net.multipart({"chat_id": str(chat_id), "caption": caption}, {"document": (name, content)})
        url = f"{self.base}/bot{self.token}/sendDocument"
        _, raw, _ = net.request("POST", url, {"Content-Type": ctype}, body, timeout=120)
        if not json.loads(raw.decode("utf-8")).get("ok"):
            raise TGError("sendDocument failed")

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        try:
            self.call("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})
        except Exception:  # noqa: BLE001
            pass
