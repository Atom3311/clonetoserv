"""SQLite: схема и тонкие помощники. Ничего не удаляется насовсем — только помечается deleted=1."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import util

TABLES = {"tx", "tasks", "debts", "stashes", "stash_moves", "recurring", "journal",
          "categories", "changes", "settings", "rates"}
SOFT = {"tx", "tasks", "debts", "stashes", "recurring", "journal"}  # у этих таблиц есть deleted

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS categories(
    name TEXT PRIMARY KEY, emoji TEXT DEFAULT '🏷', budget REAL
);
CREATE TABLE IF NOT EXISTS rates(
    code TEXT NOT NULL, date TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(code, date)
);
CREATE TABLE IF NOT EXISTS tx(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('expense','income')),
    amount REAL NOT NULL,                 -- в базовой валюте
    currency TEXT NOT NULL DEFAULT 'RUB', -- в какой валюте назвал пользователь
    orig_amount REAL NOT NULL,            -- сумма в этой валюте
    category TEXT NOT NULL,
    description TEXT DEFAULT '',
    date TEXT NOT NULL,
    raw TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tx_date ON tx(date);
CREATE TABLE IF NOT EXISTS tasks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due TEXT,
    remind_at TEXT,
    reminded INTEGER DEFAULT 0,
    done INTEGER DEFAULT 0,
    done_at TEXT,
    created_at TEXT NOT NULL,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS debts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('owed_to_me','i_owe')),
    amount REAL NOT NULL,
    paid REAL DEFAULT 0,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    closed INTEGER DEFAULT 0,
    forgiven INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stashes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target REAL,
    deadline TEXT,
    currency TEXT NOT NULL DEFAULT 'RUB',
    created_at TEXT NOT NULL,
    closed INTEGER DEFAULT 0,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stash_moves(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stash_id INTEGER NOT NULL,
    delta REAL NOT NULL,        -- в валюте копилки
    base_delta REAL NOT NULL,   -- сколько свободных денег это сняло/вернуло (в базовой валюте)
    date TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recurring(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('expense','income')),
    amount REAL NOT NULL DEFAULT 0,          -- 0 = просто срок (домен, гарантия, абонемент)
    currency TEXT NOT NULL DEFAULT 'RUB',
    period TEXT NOT NULL DEFAULT 'month',    -- month | year | once
    next_due TEXT NOT NULL,
    anchor INTEGER,
    category TEXT DEFAULT 'Другое',
    active INTEGER DEFAULT 1,
    notified TEXT DEFAULT '',
    nag INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    value REAL,
    date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS changes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id INTEGER,
    action TEXT NOT NULL,
    summary TEXT DEFAULT '',
    before TEXT,
    after TEXT,
    undone INTEGER DEFAULT 0
);
"""

DEFAULT_CATEGORIES = [
    ("Еда", "🍕"), ("Транспорт", "🚗"), ("Жильё", "🏠"), ("Связь", "📱"), ("Здоровье", "💊"),
    ("Одежда", "👟"), ("Развлечения", "🎮"), ("Подписки", "📺"), ("Учёба", "🎓"),
    ("Подарки", "🎁"), ("Зарплата", "💼"), ("Подработка", "💻"), ("Другое", "📦"),
]

EMOJI_HINTS = {
    "еда": "🍕", "продукт": "🛒", "кафе": "☕", "транспорт": "🚗", "такси": "🚕", "жиль": "🏠",
    "аренд": "🏠", "связь": "📱", "здоров": "💊", "аптек": "💊", "одежд": "👟", "развлеч": "🎮",
    "подписк": "📺", "учёб": "🎓", "учеб": "🎓", "подар": "🎁", "зарплат": "💼", "аванс": "💼",
    "подработ": "💻", "фриланс": "💻", "питом": "🐱", "спорт": "🏋️", "красот": "💇",
    "дом": "🛋", "путешеств": "✈️", "отпуск": "🏖", "техник": "💻", "образован": "🎓",
    "сервер": "🖥", "хостинг": "🖥", "игр": "🎮", "стипенд": "🎓",
}


def emoji_for(name: str) -> str:
    n = name.casefold()
    for k, e in EMOJI_HINTS.items():
        if k in n:
            return e
    return "🏷"


def clean_category(name) -> str:
    s = " ".join(str(name or "").split())[:30]
    if not s:
        return "Другое"
    return s[:1].upper() + s[1:]


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        if not self.q("SELECT 1 FROM categories LIMIT 1"):
            for n, e in DEFAULT_CATEGORIES:
                self.conn.execute("INSERT INTO categories(name, emoji) VALUES(?,?)", (n, e))
            self.conn.commit()

    # ---- базовое ----
    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        return self.conn.execute(sql, args).fetchone()

    def exec(self, sql: str, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur

    def insert(self, table: str, **f) -> int:
        assert table in TABLES
        cols = ",".join(f)
        marks = ",".join("?" for _ in f)
        cur = self.conn.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", tuple(f.values()))
        self.conn.commit()
        return cur.lastrowid

    def update(self, table: str, rid: int, **f) -> None:
        assert table in TABLES
        if not f:
            return
        sets = ",".join(f"{k}=?" for k in f)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*f.values(), rid))
        self.conn.commit()

    def get(self, table: str, rid: int) -> dict | None:
        assert table in TABLES
        r = self.one(f"SELECT * FROM {table} WHERE id=?", (rid,))
        return dict(r) if r else None

    def live(self, table: str, rid: int) -> dict | None:
        row = self.get(table, rid)
        if row and row.get("deleted"):
            return None
        return row

    # ---- настройки ----
    def setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value) -> None:
        self.exec("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    def del_setting(self, key: str) -> None:
        self.exec("DELETE FROM settings WHERE key=?", (key,))

    # ---- журнал изменений (на нём держатся «отмени» и «верни») ----
    def log_change(self, entity: str, entity_id: int, action: str, summary: str = "",
                   before: dict | None = None, after: dict | None = None) -> int:
        dump = lambda d: json.dumps(d, ensure_ascii=False, default=str) if d is not None else None
        return self.insert("changes", ts=util.stamp(), entity=entity, entity_id=entity_id,
                           action=action, summary=summary, before=dump(before), after=dump(after))

    # ---- категории ----
    def find_category(self, name: str) -> dict | None:
        cf = clean_category(name).casefold()
        for r in self.q("SELECT * FROM categories"):
            if r["name"].casefold() == cf:
                return dict(r)
        return None

    def ensure_category(self, name, emoji: str | None = None) -> tuple[str, bool]:
        """Вернёт (каноничное имя, создана ли новая)."""
        found = self.find_category(name)
        if found:
            return found["name"], False
        cname = clean_category(name)
        e = (str(emoji).strip()[:4] if emoji else "") or emoji_for(cname)
        self.exec("INSERT INTO categories(name, emoji) VALUES(?,?)", (cname, e))
        return cname, True

    def emojis(self) -> dict[str, str]:
        return {r["name"]: r["emoji"] or "🏷" for r in self.q("SELECT name, emoji FROM categories")}

    def category_names(self) -> list[str]:
        return [r["name"] for r in self.q("SELECT name FROM categories ORDER BY rowid")]

    # ---- копилки ----
    def find_stash(self, name: str, include_closed: bool = False) -> dict | None:
        rows = self.q("SELECT * FROM stashes WHERE deleted=0" + ("" if include_closed else " AND closed=0"))
        cf = " ".join(str(name or "").split())
        if not cf:
            return None
        best, best_s = None, 0
        for r in rows:
            s = util.match_score(cf, r["name"])
            if s > best_s:
                best, best_s = dict(r), s
        return best

    # ---- бэкап ----
    def backup_to(self, path: str) -> None:
        dest = sqlite3.connect(path)
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
