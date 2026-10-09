"""Курсы валют. Автоматически — ЦБ РФ (бесплатно), либо вручную: «курс доллара 95»."""
from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET

import config
import util


class NoRate(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "life-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_json_rates(raw: bytes) -> dict[str, float]:
    data = json.loads(raw.decode("utf-8"))
    out = {}
    for code, v in data["Valute"].items():
        out[code.upper()] = float(v["Value"]) / float(v.get("Nominal", 1) or 1)
    return out


def parse_xml_rates(raw: bytes) -> dict[str, float]:
    root = ET.fromstring(raw)
    out = {}
    for v in root.iter("Valute"):
        code = v.findtext("CharCode")
        nominal = float((v.findtext("Nominal") or "1").replace(",", "."))
        value = float((v.findtext("Value") or "0").replace(",", "."))
        if code and value:
            out[code.upper()] = value / nominal
    return out


def fetch_cbr() -> dict[str, float]:
    """Сколько рублей стоит 1 единица валюты. Пробует зеркало в JSON, потом официальный XML."""
    errors = []
    for url, parser in ((config.RATES_URL, parse_json_rates), (config.RATES_URL_2, parse_xml_rates)):
        if not url:
            continue
        try:
            data = parser(_get(url))
            if data:
                return data
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    raise RuntimeError("не удалось получить курсы: " + "; ".join(errors))


class Rates:
    """Все курсы — «сколько базовой валюты за 1 единицу». Базовая валюта = 1."""

    def __init__(self, db, fetcher=fetch_cbr):
        self.db = db
        self.fetcher = fetcher

    def get(self, code: str) -> float | None:
        code = (code or util.base_cur()).upper()
        if code == util.base_cur():
            return 1.0
        manual = self.db.setting(f"rate_manual_{code}")
        if manual:
            try:
                return float(manual)
            except ValueError:
                pass
        r = self.db.one("SELECT value FROM rates WHERE code=? ORDER BY date DESC LIMIT 1", (code,))
        return float(r["value"]) if r else None

    def _lazy_refresh(self) -> bool:
        """Курса нет — один раз в 10 минут пробуем подтянуть его прямо сейчас."""
        from datetime import datetime, timedelta
        n = util.now()
        last = self.db.setting("rates_lazy", "")
        if last:
            try:
                if n - datetime.fromisoformat(last) < timedelta(minutes=10):
                    return False
            except ValueError:
                pass
        self.db.set_setting("rates_lazy", n.isoformat(sep=" ", timespec="minutes"))
        try:
            return self.refresh()
        except Exception:  # noqa: BLE001
            return False

    def _need(self, code: str) -> float:
        rate = self.get(code)
        if rate is None and self._lazy_refresh():
            rate = self.get(code)
        if rate is None:
            raise NoRate(code.upper())
        return rate

    def to_base(self, amount: float, code: str) -> tuple[float, float]:
        rate = self._need(code)
        return round(float(amount) * rate, 2), rate

    def convert(self, amount: float, src: str, dst: str) -> float:
        base, _ = self.to_base(amount, src)
        return round(base / self._need(dst), 6)

    def set_manual(self, code: str, value: float) -> None:
        self.db.set_setting(f"rate_manual_{code.upper()}", value)

    def clear_manual(self, code: str) -> None:
        self.db.del_setting(f"rate_manual_{code.upper()}")

    def is_manual(self, code: str) -> bool:
        return self.db.setting(f"rate_manual_{code.upper()}") is not None

    def refresh(self) -> bool:
        data = self.fetcher()
        today = util.today().isoformat()
        base = util.base_cur()
        # курсы ЦБ даны в рублях; для другой базовой валюты пересчитываем
        if base != "RUB":
            if base not in data:
                raise RuntimeError(f"в списке ЦБ нет {base}")
            k = data[base]
            data = {c: v / k for c, v in data.items() if c != base}
            data["RUB"] = 1.0 / k
        for code, val in data.items():
            self.db.conn.execute("INSERT OR REPLACE INTO rates(code,date,value) VALUES(?,?,?)",
                                 (code, today, val))
        self.db.conn.commit()
        self.db.set_setting("rates_ok", util.stamp())
        return True

    def known(self) -> list[str]:
        rows = self.db.q("SELECT DISTINCT code FROM rates ORDER BY code")
        return [r["code"] for r in rows]
