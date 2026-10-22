"""Сверка тегов e6ai с правками модераторов.

После загрузки запоминаем, какие теги отправили. Через сутки (и ещё раз через трое) забираем с e6ai
итоговые теги поста и сравниваем:
  • что модераторы убрали — больше не отмечается само (подсказка остаётся пунктиром);
  • что добавили (и что сайт не добавил бы сам) — предлагается охотнее и отмечается от 25% уверенности,
    если модераторы добавляли тег хотя бы дважды.
Персонажей, франшизы, директоров и мета-теги не запоминаем: только общие теги и виды.
"""
import json
from datetime import datetime, timedelta, timezone

from app import db

LEARN_CATS = {"general", "species"}
AUTO = {"hi_res", "absurd_res", "superabsurd_res", "low_res", "animated", "webm", "mp4", "short_playtime", "long_playtime",
        "mid_length_playtime", "sound", "no_sound", "tagme", "ai_generated", "thumbnail"}
CHECKS = (24, 72)   # через сколько часов после загрузки сверяемся

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS e6_sent (
    post_id INTEGER PRIMARY KEY, pub_id INTEGER, sent TEXT, at TEXT,
    checks INTEGER DEFAULT 0, checked_at TEXT, removed TEXT, added TEXT, final_cats TEXT
);
CREATE TABLE IF NOT EXISTS e6_learn (tag TEXT PRIMARY KEY, removed INTEGER DEFAULT 0, added INTEGER DEFAULT 0, last TEXT);
""")
def _migrate_site():
    """Пост определяется парой «сайт + номер»: номера на e6ai и e621 могут совпасть. Старая таблица (ключ — только номер)
    переносится в новую один раз, все прежние записи — с e6ai."""
    cols = [r["name"] for r in db.query("PRAGMA table_info(e6_sent)")]
    pk = [r["name"] for r in db.query("PRAGMA table_info(e6_sent)") if r["pk"]]
    if sorted(pk) == ["post_id", "site"]:
        return
    with db.tx() as c:
        c.execute("""CREATE TABLE e6_sent_new (
            site TEXT NOT NULL DEFAULT 'e6ai', post_id INTEGER NOT NULL, pub_id INTEGER, sent TEXT, at TEXT,
            checks INTEGER DEFAULT 0, checked_at TEXT, removed TEXT, added TEXT, final_cats TEXT, PRIMARY KEY (site, post_id))""")
        keep = "site, " if "site" in cols else ""
        c.execute(f"""INSERT OR IGNORE INTO e6_sent_new (site, post_id, pub_id, sent, at, checks, checked_at, removed, added, final_cats)
                      SELECT {keep if keep else "'e6ai', "}post_id, pub_id, sent, at, checks, checked_at, removed, added, final_cats FROM e6_sent""")
        c.execute("DROP TABLE e6_sent")
        c.execute("ALTER TABLE e6_sent_new RENAME TO e6_sent")


_migrate_site()


def _now():
    return datetime.now(timezone.utc)


def record(pub_id: int, post_id, tags: list[str], site: str = "e6ai"):
    """Запомнить, что отправили (вызывается после каждой загрузки на e6ai или e621)."""
    try:
        pid = int(post_id)
    except (TypeError, ValueError):
        return
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO e6_sent (post_id, pub_id, sent, at, site) VALUES (?,?,?,?,?)",
                  (pid, pub_id, json.dumps(sorted(set(tags))), _now().isoformat(timespec="seconds"), site))


def learned() -> dict:
    """{тег: {"removed": n, "added": n}}"""
    return {r["tag"]: {"removed": r["removed"], "added": r["added"]} for r in db.query("SELECT * FROM e6_learn")}


def forget(tag: str | None = None):
    with db.tx() as c:
        if tag:
            c.execute("DELETE FROM e6_learn WHERE tag=?", (tag,))
        else:
            c.execute("DELETE FROM e6_learn")


def _fetch(post_id: int, site: str = "e6ai") -> dict | None:
    """{категория: [теги]} с сайта поста (e6ai или e621) или None."""
    import os
    import requests
    from app.publish import settings as PS
    from app.publish import e6ai as E6
    raw = PS.load(raw=True)["e6ai"]
    acc = raw if site == "e6ai" else ((raw.get("accounts") or {}).get(site) or {})
    cfg = dict(raw, username=acc.get("username", ""), api_key=acc.get("api_key", ""), site_name=site,
               base=os.environ.get("CROSSPOST_E6AI_BASE") or PS.BOORU.get(site, PS.BOORU["e6ai"]))
    auth = (cfg["username"], cfg["api_key"]) if cfg.get("username") and cfg.get("api_key") else None
    r = requests.get(f"{E6.base(cfg)}/posts/{post_id}.json", headers=E6._ua(cfg), auth=auth, timeout=20)
    if r.status_code != 200:
        return None
    return (r.json().get("post") or {}).get("tags") or None


def diff(sent: list[str], tags_by_cat: dict) -> tuple[list, list, dict]:
    """(убрали, добавили, категории итоговых тегов)."""
    from app import e6tags
    cats = {t: c for c, lst in (tags_by_cat or {}).items() for t in lst}
    final = set(cats)
    sent_c = {e6tags.canonical(t) for t in sent}
    implied = set(e6tags.implied(list(sent_c))) if e6tags.ready() else set()
    removed = sorted(t for t in sent_c if t not in final)
    added = {t for t in final if t not in sent_c and t not in implied and t not in AUTO}
    if e6tags.ready():   # франшизу к добавленному персонажу сайт дописал сам — это не правка модератора
        added -= set(e6tags.implied(list(added)))
    added = sorted(added)
    return removed, added, cats


def check_due(limit: int = 10) -> int:
    """Сверить посты, у которых подошёл срок. Возвращает, сколько сверено."""
    from app.publish import settings as PS
    if not PS.module_on("e6ai"):   # e6ai выключен — сайт не опрашиваем
        return 0
    now = _now()
    rows = db.query("SELECT * FROM e6_sent WHERE checks < ? ORDER BY at LIMIT 200", (len(CHECKS),))
    n = 0
    for r in rows:
        age_h = (now - datetime.fromisoformat(r["at"])).total_seconds() / 3600
        if age_h < CHECKS[r["checks"] or 0] or n >= limit:
            continue
        try:
            tags = _fetch(r["post_id"], r.get("site") or "e6ai")
        except Exception:
            continue
        if tags is None:   # пост удалили или сеть — попробуем в следующий раз, но не вечно
            if age_h > 24 * 14:
                with db.tx() as c:
                    c.execute("UPDATE e6_sent SET checks=? WHERE post_id=? AND site=?", (len(CHECKS), r["post_id"], r["site"]))
            continue
        removed, added, cats = diff(json.loads(r["sent"] or "[]"), tags)
        old_r, old_a = set(json.loads(r["removed"] or "[]")), set(json.loads(r["added"] or "[]"))
        stamp = now.isoformat(timespec="seconds")
        with db.tx() as c:
            for t in set(removed) - old_r:   # учим только новое с прошлой сверки
                if cats.get(t, _cat(t)) in LEARN_CATS:
                    c.execute("INSERT INTO e6_learn (tag, removed, last) VALUES (?,1,?) ON CONFLICT(tag) DO UPDATE SET removed=removed+1, last=?",
                              (t, stamp, stamp))
            for t in set(added) - old_a:
                if cats.get(t) in LEARN_CATS:
                    c.execute("INSERT INTO e6_learn (tag, added, last) VALUES (?,1,?) ON CONFLICT(tag) DO UPDATE SET added=added+1, last=?",
                              (t, stamp, stamp))
            c.execute("UPDATE e6_sent SET checks=checks+1, checked_at=?, removed=?, added=?, final_cats=? WHERE post_id=? AND site=?",
                      (stamp, json.dumps(removed), json.dumps(added), json.dumps({t: cats[t] for t in added}), r["post_id"], r["site"]))
        n += 1
    return n


def _cat(tag: str) -> str:
    from app import e6tags
    i = e6tags.info([tag]).get(tag) or {}
    return i.get("cat") or "general"


def for_publication(pub_id: int) -> list[dict]:
    """Что модераторы поменяли в постах этой публикации."""
    out = []
    for r in db.query("SELECT * FROM e6_sent WHERE pub_id=? ORDER BY post_id", (pub_id,)):
        out.append({"post_id": r["post_id"], "checked": bool(r["checks"]), "checked_at": r["checked_at"],
                    "removed": json.loads(r["removed"] or "[]"), "added": json.loads(r["added"] or "[]"),
                    "due": (datetime.fromisoformat(r["at"]) + timedelta(hours=CHECKS[0])).isoformat() if not r["checks"] else None})
    return out
