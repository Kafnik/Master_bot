"""
halloween_db.py — отдельная база события «Хэллоуин» и единственная точка доступа к ней.

Как это устроено
  • Файл базы: Halloween_event.db (лежит рядом с этим модулем).
  • Читает и меняет эту базу ТОЛЬКО этот модуль. Основной бот (test.py) вызывает функции отсюда
    и никогда не пишет SQL к базе события сам.
  • Модуль ничего не знает про telebot и про основную базу бота. Монеты, XP и эмодзи в инвентаре
    остаются в основной базе — их меняет сам бот. Единственное исключение — одноразовый перенос
    старых данных: migrate_from_main_db() читает основную базу в режиме «только чтение».
  • Все функции потокобезопасны (бот работает в нескольких потоках).

Что хранится в базе события
  players        — Жуткие ключи игроков
  case_inventory — кейсы, которые лежат у игрока (купленные, но ещё не открытые)
  titles         — купленные хэллоуинские титулы
  event_log      — журнал всех изменений (кто, когда, что и на сколько изменилось)
  meta           — служебные флаги (например, что старые данные уже перенесены)

Быстрая проверка из консоли:  python halloween_db.py
"""

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB_NAME = "Halloween_event.db"

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_db_path: Optional[str] = None


# ============================================================
#  Подключение и создание таблиц
# ============================================================
def init(db_path=None):
    """Открывает базу события и создаёт таблицы, если их ещё нет.

    db_path:
      • None — берётся переменная окружения HALLOWEEN_DB или Halloween_event.db;
      • относительный путь считается от папки, где лежит этот файл;
      • абсолютный путь используется как есть.
    Повторный вызов с тем же путём ничего не ломает.
    """
    global _conn, _db_path
    with _lock:
        path = db_path or os.environ.get("HALLOWEEN_DB") or DEFAULT_DB_NAME
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)

        if _conn is not None and _db_path == path:
            return path
        if _conn is not None:
            _conn.close()

        _conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        _db_path = path
        _create_tables(_conn)
        return path


def get_db_path():
    """Путь к файлу базы события (открывает базу, если она ещё не открыта)."""
    _c()
    return _db_path


def _c() -> sqlite3.Connection:
    """Соединение с базой; если init() не вызывали — открываем с настройками по умолчанию."""
    if _conn is None:
        init()
    assert _conn is not None  # после init() соединение всегда есть (и это подсказка для Pylance)
    return _conn


def _create_tables(c):
    with c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS players (
                user_id      INTEGER PRIMARY KEY,
                spooky_keys  INTEGER NOT NULL DEFAULT 0 CHECK (spooky_keys >= 0),
                created_at   TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS case_inventory (
                user_id   INTEGER NOT NULL,
                case_key  TEXT    NOT NULL,
                count     INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
                PRIMARY KEY (user_id, case_key)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS titles (
                user_id    INTEGER NOT NULL,
                title_key  TEXT    NOT NULL,
                price      INTEGER DEFAULT 0,
                bought_at  TEXT,
                PRIMARY KEY (user_id, title_key)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       TEXT,
                user_id  INTEGER,
                action   TEXT,
                amount   INTEGER DEFAULT 0,
                detail   TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_event_log_user ON event_log (user_id, id)")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _log(c, user_id, action, amount=0, detail=""):
    c.execute(
        "INSERT INTO event_log (ts, user_id, action, amount, detail) VALUES (?, ?, ?, ?, ?)",
        (_now(), user_id, action, int(amount), str(detail or "")),
    )


def _check_amount(amount):
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError(f"amount должен быть целым числом больше 0, получено: {amount!r}")


# ============================================================
#  🗝️ Жуткие ключи
# ============================================================
def get_keys(user_id):
    """Сколько Жутких ключей у игрока."""
    with _lock:
        row = _c().execute(
            "SELECT spooky_keys FROM players WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row[0]) if row else 0


def add_keys(user_id, amount=1, reason=""):
    """Выдаёт ключи. Возвращает новый баланс ключей."""
    _check_amount(amount)
    with _lock:
        c = _c()
        with c:
            c.execute(
                "INSERT INTO players (user_id, spooky_keys, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET spooky_keys = spooky_keys + excluded.spooky_keys",
                (user_id, amount, _now()),
            )
            _log(c, user_id, "keys+", amount, reason)
        return get_keys(user_id)


def spend_keys(user_id, amount=1, reason=""):
    """Списывает ключи. True — списано; False — ключей не хватает (ничего не меняется)."""
    _check_amount(amount)
    with _lock:
        c = _c()
        with c:
            cur = c.execute(
                "UPDATE players SET spooky_keys = spooky_keys - ? "
                "WHERE user_id = ? AND spooky_keys >= ?",
                (amount, user_id, amount),
            )
            if cur.rowcount != 1:
                return False
            _log(c, user_id, "keys-", amount, reason)
            return True


# ============================================================
#  🎁 Кейсы игрока
# ============================================================
def get_case_count(user_id, case_key):
    """Сколько таких кейсов лежит у игрока."""
    with _lock:
        row = _c().execute(
            "SELECT count FROM case_inventory WHERE user_id = ? AND case_key = ?",
            (user_id, case_key),
        ).fetchone()
        return int(row[0]) if row else 0


def add_case(user_id, case_key, amount=1, reason=""):
    """Кладёт кейс(ы) игроку. Возвращает новое количество таких кейсов."""
    _check_amount(amount)
    with _lock:
        c = _c()
        with c:
            c.execute(
                "INSERT INTO case_inventory (user_id, case_key, count) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, case_key) DO UPDATE SET count = count + excluded.count",
                (user_id, case_key, amount),
            )
            _log(c, user_id, "case+", amount, f"{case_key} {reason}".strip())
        return get_case_count(user_id, case_key)


def take_case(user_id, case_key, reason=""):
    """Забирает один кейс (перед открытием). True — забрали; False — такого кейса нет."""
    with _lock:
        c = _c()
        with c:
            cur = c.execute(
                "UPDATE case_inventory SET count = count - 1 "
                "WHERE user_id = ? AND case_key = ? AND count > 0",
                (user_id, case_key),
            )
            if cur.rowcount != 1:
                return False
            _log(c, user_id, "case-", 1, f"{case_key} {reason}".strip())
            return True


# ============================================================
#  🏆 Хэллоуинские титулы
# ============================================================
def record_title(user_id, title_key, price=0):
    """Записывает, что игрок купил титул. True — новая запись; False — уже был записан."""
    with _lock:
        c = _c()
        with c:
            cur = c.execute(
                "INSERT OR IGNORE INTO titles (user_id, title_key, price, bought_at) VALUES (?, ?, ?, ?)",
                (user_id, title_key, int(price), _now()),
            )
            if cur.rowcount != 1:
                return False
            _log(c, user_id, "title", price, title_key)
            return True


def get_titles(user_id):
    """Список ключей титулов, которые покупал игрок (в порядке покупки)."""
    with _lock:
        rows = _c().execute(
            "SELECT title_key FROM titles WHERE user_id = ? ORDER BY bought_at, rowid",
            (user_id,),
        ).fetchall()
        return [r[0] for r in rows]


# ============================================================
#  📜 Журнал и сводка
# ============================================================
def get_log(user_id=None, limit=20):
    """Последние записи журнала (сначала новые). user_id=None — по всем игрокам."""
    with _lock:
        if user_id is None:
            rows = _c().execute(
                "SELECT ts, user_id, action, amount, detail FROM event_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        else:
            rows = _c().execute(
                "SELECT ts, user_id, action, amount, detail FROM event_log "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, int(limit)),
            ).fetchall()
        return [
            {"ts": r[0], "user_id": r[1], "action": r[2], "amount": r[3], "detail": r[4]}
            for r in rows
        ]


def get_summary():
    """Короткая сводка по базе события."""
    with _lock:
        c = _c()
        one = lambda sql: c.execute(sql).fetchone()[0] or 0
        return {
            "players": one("SELECT COUNT(*) FROM players"),
            "keys_total": one("SELECT SUM(spooky_keys) FROM players"),
            "cases_total": one("SELECT SUM(count) FROM case_inventory"),
            "titles_bought": one("SELECT COUNT(*) FROM titles"),
            "log_rows": one("SELECT COUNT(*) FROM event_log"),
        }


# ============================================================
#  🚚 Одноразовый перенос старых данных из основной базы бота
# ============================================================
def _meta_get(c, key):
    row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def migrate_from_main_db(main_db_path, title_keys=None):
    """Один раз переносит данные события из основной базы бота в базу события.

    Переносится: users.spooky_keys, таблица case_inventory и (если передан title_keys) текущий
    хэллоуинский титул игрока из users.profile_title. Основная база открывается только на чтение
    и НЕ меняется — старые столбцы остаются как резервная копия.
    Повторные вызовы ничего не делают (флаг хранится в таблице meta).

    Возвращает словарь с итогом: {"done": True, "keys_players": ..., "cases_rows": ..., "titles": ...}
    либо {"done": False, "reason": "..."}.
    """
    with _lock:
        c = _c()
        if _meta_get(c, "migrated_from_main"):
            return {"done": False, "reason": "уже перенесено раньше"}
        if not main_db_path or not os.path.exists(main_db_path):
            return {"done": False, "reason": "основная база не найдена"}

        uri = Path(main_db_path).resolve().as_uri() + "?mode=ro"
        src = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            user_cols = set()
            if "users" in tables:
                user_cols = {r[1] for r in src.execute("PRAGMA table_info(users)")}

            keys = []
            if "spooky_keys" in user_cols:
                keys = src.execute(
                    "SELECT user_id, spooky_keys FROM users WHERE COALESCE(spooky_keys, 0) > 0"
                ).fetchall()

            cases = []
            if "case_inventory" in tables:
                cases = src.execute(
                    "SELECT user_id, case_key, count FROM case_inventory WHERE COALESCE(count, 0) > 0"
                ).fetchall()

            titles = []
            if title_keys and "profile_title" in user_cols:
                marks = ",".join("?" for _ in title_keys)
                titles = src.execute(
                    f"SELECT user_id, profile_title FROM users WHERE profile_title IN ({marks})",
                    list(title_keys),
                ).fetchall()
        finally:
            src.close()

        with c:
            for user_id, amount in keys:
                c.execute(
                    "INSERT INTO players (user_id, spooky_keys, created_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET spooky_keys = spooky_keys + excluded.spooky_keys",
                    (user_id, int(amount), _now()),
                )
                _log(c, user_id, "migrate_keys", amount, "из основной базы")
            for user_id, case_key, count in cases:
                c.execute(
                    "INSERT INTO case_inventory (user_id, case_key, count) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id, case_key) DO UPDATE SET count = count + excluded.count",
                    (user_id, case_key, int(count)),
                )
                _log(c, user_id, "migrate_case", count, case_key)
            for user_id, title_key in titles:
                c.execute(
                    "INSERT OR IGNORE INTO titles (user_id, title_key, price, bought_at) VALUES (?, ?, 0, ?)",
                    (user_id, title_key, _now()),
                )
                _log(c, user_id, "migrate_title", 0, title_key)
            c.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('migrated_from_main', ?)", (_now(),)
            )

        return {
            "done": True,
            "keys_players": len(keys),
            "cases_rows": len(cases),
            "titles": len(titles),
        }


# ============================================================
#  Проверка из консоли:  python halloween_db.py
# ============================================================
if __name__ == "__main__":
    print("База события:", init())
    summary = get_summary()
    print("Сводка:")
    for name, value in summary.items():
        print(f"  {name}: {value}")
    print("Последние записи журнала:")
    for row in get_log(limit=10):
        print(f"  {row['ts']}  user={row['user_id']}  {row['action']} {row['amount']}  {row['detail']}")