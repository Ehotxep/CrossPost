"""База постов: ваши публикации со всех площадок и их цифры (SQLite в папке данных)."""
import sqlite3
import threading
from contextlib import contextmanager

from app.paths import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    platform  TEXT NOT NULL,          -- telegram, vk, ...
    chat_id   TEXT NOT NULL,
    title     TEXT,
    username  TEXT,
    rating    TEXT DEFAULT 'all',     -- sfw / nsfw / all — какой контент там публикуется
    selected  INTEGER DEFAULT 0,      -- участвует ли в импорте и статистике
    last_msg_id INTEGER DEFAULT 0,    -- до какого сообщения импортировано
    synced_at TEXT,
    PRIMARY KEY (platform, chat_id)
);
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    platform   TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    msg_id     INTEGER NOT NULL,      -- для альбома — первое сообщение
    grouped_id INTEGER,
    msg_ids    TEXT,                  -- все сообщения альбома через запятую
    date_utc   TEXT NOT NULL,
    text       TEXT,
    media_type TEXT,                  -- photo / video / gif / document / text
    n_media    INTEGER DEFAULT 0,
    views      INTEGER,
    forwards   INTEGER,
    replies    INTEGER,
    reactions  INTEGER,
    link       TEXT,
    preview    TEXT,                  -- имя файла превью в папке previews
    is_forward INTEGER DEFAULT 0,     -- пересланный из другого канала
    updated_at TEXT,
    UNIQUE (platform, chat_id, msg_id)
);
CREATE INDEX IF NOT EXISTS posts_chat_date ON posts (platform, chat_id, date_utc);
CREATE TABLE IF NOT EXISTS messages (      -- отдельные сообщения (у альбома их несколько на один пост)
    platform  TEXT NOT NULL,
    chat_id   TEXT NOT NULL,
    msg_id    INTEGER NOT NULL,
    post_id   INTEGER NOT NULL,
    media_type TEXT,
    views INTEGER, forwards INTEGER, replies INTEGER, reactions INTEGER,
    PRIMARY KEY (platform, chat_id, msg_id)
);
CREATE TABLE IF NOT EXISTS post_tags (       -- теги картинки поста от теггера (по превью)
    post_id INTEGER NOT NULL, tag TEXT NOT NULL, prob REAL,
    PRIMARY KEY (post_id, tag)
);
CREATE INDEX IF NOT EXISTS post_tags_tag ON post_tags (tag);
CREATE TABLE IF NOT EXISTS post_tagged (     -- какие посты уже размечены (в т.ч. без тегов)
    post_id INTEGER PRIMARY KEY, at TEXT
);
CREATE TABLE IF NOT EXISTS forecasts (       -- прогноз, сделанный перед публикацией — потом сравниваем с фактом
    pub_id INTEGER NOT NULL, chat_id TEXT NOT NULL, made_at TEXT, data TEXT,
    PRIMARY KEY (pub_id, chat_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    post_id   INTEGER NOT NULL,
    taken_at  TEXT NOT NULL,
    views     INTEGER, forwards INTEGER, replies INTEGER, reactions INTEGER
);
CREATE INDEX IF NOT EXISTS snapshots_post ON snapshots (post_id, taken_at);
"""

_lock = threading.RLock()
_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(data_dir() / "posts.db", check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            try:   # окно приложения и фоновая отправка пишут в базу одновременно
                _conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(c: sqlite3.Connection):
    """Добавляет новые столбцы в уже существующую базу."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(posts)")}
    if "excluded" not in cols:   # пост помечен «не учитывать» (анонс, новость и т.п.)
        c.execute("ALTER TABLE posts ADD COLUMN excluded INTEGER DEFAULT 0")
    c.commit()


@contextmanager
def tx():
    """Транзакция под общей блокировкой (к базе обращаются и интерфейс, и фоновый импорт)."""
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def query(sql: str, args=()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def reset_for_tests():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
