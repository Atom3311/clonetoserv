"""Расписание: напоминания, платежи и сроки, утро, итоги недели, бэкап, курсы.

Все проверки идемпотентны (флаги лежат в базе), поэтому после перезапуска ничего не дублируется,
а пропущенное за время простоя дойдёт при следующей проверке.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import calc
import config
import util
import views
from engine import Reply

log = logging.getLogger("jobs")


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _after(n, hhmm: str) -> bool:
    h, m = _hm(hhmm)
    return (n.hour, n.minute) >= (h, m)


class Jobs:
    def __init__(self, db, rates, engine, send, send_doc):
        """send(replies_or_text) шлёт в основной чат; send_doc(path, caption) — файл."""
        self.db, self.rates, self.engine = db, rates, engine
        self.send, self.send_doc = send, send_doc

    # ------------------------------------------------------------------
    def tick(self) -> None:
        n = util.now()
        for step in (self.task_reminders, self.rates_job, self.morning_job, self.recurring_job,
                     self.weekly_job, self.backup_job):
            try:
                step(n)
            except Exception:  # noqa: BLE001
                log.exception("job %s failed", step.__name__)

    # ------------------------------------------------------------------
    def task_reminders(self, n) -> None:
        stamp = n.strftime("%Y-%m-%d %H:%M")
        rows = self.db.q("SELECT * FROM tasks WHERE deleted=0 AND done=0 AND reminded=0 "
                         "AND remind_at IS NOT NULL AND remind_at<=? ORDER BY remind_at", (stamp,))
        for t in rows:
            late = ""
            at = util.to_datetime(t["remind_at"])
            if at and (n - at) > timedelta(hours=2):
                late = f"\n<i>(должно было прийти {util.fmt_due(t['remind_at'])})</i>"
            due = f"\n📅 Срок: {util.fmt_due(t['due'])}" if t["due"] and not late else ""
            text = f"⏰ <b>Напоминание</b>\n{util.esc(t['text'])}{due}{late}"
            kb = [[("✅ Готово", f"t:done:{t['id']}"), ("⏳ +1 час", f"t:snz:{t['id']}")],
                  [("📅 Завтра", f"t:tom:{t['id']}"), ("🗑 Удалить", f"t:del:{t['id']}")]]
            self.send([Reply(text, kb)])
            self.db.update("tasks", t["id"], reminded=1)

    # ------------------------------------------------------------------
    def rates_job(self, n) -> None:
        today = n.date().isoformat()
        ok = self.db.setting("rates_ok", "")
        if ok.startswith(today):
            return
        last_try = self.db.setting("rates_try", "")
        if last_try:
            try:
                from datetime import datetime
                if n - datetime.fromisoformat(last_try) < timedelta(hours=3):
                    return
            except ValueError:
                pass
        self.db.set_setting("rates_try", n.isoformat(sep=" ", timespec="minutes"))
        try:
            self.rates.refresh()
            log.info("курсы обновлены")
        except Exception as e:  # noqa: BLE001
            log.warning("курсы не обновились: %s", e)

    # ------------------------------------------------------------------
    def recurring_job(self, n) -> None:
        if not _after(n, config.MORNING_TIME):
            return
        today = n.date()
        for r in self.db.q("SELECT * FROM recurring WHERE active=1 AND deleted=0 ORDER BY next_due"):
            r = dict(r)
            d = util.parse_date(r["next_due"])
            if not d:
                continue
            delta = (d - today).days
            if delta in config.LEAD_DAYS:
                key = f"{r['next_due']}:{delta}"
                if r["notified"] != key:
                    self.send([self.engine.recurring_heads_up(r, delta)])
                    self.db.update("recurring", r["id"], notified=key)
            elif delta <= 0:
                key = f"{r['next_due']}:due:{today.isoformat()}"
                if r["notified"] == key:
                    continue
                if r["nag"] >= 5:
                    if r["nag"] == 5:
                        self.send([Reply(f"🤐 Я уже 5 раз спрашивал про «{util.esc(r['name'])}» и не получил ответа. "
                                         f"Больше не напоминаю. Скажи «{util.esc(r['name'])} оплатил» или «отключи».")])
                        self.db.update("recurring", r["id"], nag=6, notified=key)
                    continue
                self.send([self.engine.recurring_prompt(r, -delta)])
                self.db.update("recurring", r["id"], nag=r["nag"] + 1, notified=key)

    # ------------------------------------------------------------------
    def _has_data(self) -> bool:
        for tbl in ("tx", "tasks", "recurring", "debts"):
            if self.db.one(f"SELECT 1 FROM {tbl} WHERE deleted=0 LIMIT 1"):
                return True
        return False

    def morning_job(self, n) -> None:
        today = n.date().isoformat()
        if self.db.setting("last_morning") == today or not _after(n, config.MORNING_TIME):
            return
        self.db.set_setting("last_morning", today)
        h, _ = _hm(config.MORNING_TIME)
        if n.hour >= h + 6:
            return  # бот был выключен, «доброе утро» вечером не нужно
        if self.db.setting("morning", "on") != "on" or not self._has_data():
            return
        self.send([Reply(views.morning_brief(self.db, self.rates, n.date()))])

    def weekly_job(self, n) -> None:
        if n.weekday() != 6 or not _after(n, config.WEEKLY_TIME):
            return
        y, w, _ = n.date().isocalendar()
        key = f"{y}-W{w}"
        if self.db.setting("last_weekly") == key:
            return
        self.db.set_setting("last_weekly", key)
        if self.db.setting("weekly", "on") != "on" or not self._has_data():
            return
        self.send([Reply(views.weekly_review(self.db, self.rates, n.date()))])

    def backup_job(self, n) -> None:
        if n.weekday() != 6 or not _after(n, config.BACKUP_TIME):
            return
        y, w, _ = n.date().isocalendar()
        key = f"{y}-W{w}"
        if self.db.setting("last_backup") == key:
            return
        self.make_backup("Автоматический бэкап базы")
        self.db.set_setting("last_backup", key)

    def make_backup(self, caption: str, chat: int | None = None) -> str:
        folder = os.path.join(os.path.dirname(os.path.abspath(self.db.path)) if self.db.path != ":memory:" else ".",
                              "backups")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"life-{util.today().isoformat()}.db")
        self.db.backup_to(path)
        old = sorted(f for f in os.listdir(folder) if f.startswith("life-") and f.endswith(".db"))
        for f in old[:-8]:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
        self.send_doc(path, caption, chat)
        return path
