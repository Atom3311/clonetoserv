"""Минимальный HTTP-клиент на стандартной библиотеке (чтобы ничего не ставить через pip)."""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid

UA = "life-bot/1.0"


class HTTPError(Exception):
    def __init__(self, status: int, body: bytes, url: str = "", headers: dict | None = None):
        self.status = status
        self.body = body
        self.url = url
        self.headers = headers or {}
        super().__init__(f"HTTP {status}: {body[:200]!r}")

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def request(method: str, url: str, headers: dict | None = None, data: bytes | None = None,
            timeout: float = 30) -> tuple[int, bytes, dict]:
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        raise HTTPError(e.code, body, url, dict(e.headers or {})) from None


def post_json(url: str, payload: dict, headers: dict | None = None, timeout: float = 30) -> dict:
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    _, body, _ = request("POST", url, h, json.dumps(payload).encode("utf-8"), timeout)
    return json.loads(body.decode("utf-8"))


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """files = {имя_поля: (имя_файла, bytes)}. Возвращает (тело, content-type)."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    for k, (fname, content) in files.items():
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                      f'Content-Type: {ctype}\r\n\r\n').encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
