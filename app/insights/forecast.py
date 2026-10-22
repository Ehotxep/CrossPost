"""Прогноз для черновика: в какие каналы Telegram уйдёт пост и сколько там примерно наберёт."""
import json
from datetime import datetime

from app import db
from app.insights import model as M
from app.paths import sub


def _db_chat_id(ch: dict) -> str | None:
    cid = str(ch.get("id") or "").strip()
    if cid.startswith("@") or (cid and not cid.lstrip("-").isdigit()):
        r = db.query("SELECT chat_id FROM channels WHERE platform='telegram' AND lower(username)=?",
                     (cid.lstrip("@").lower(),))
        return r[0]["chat_id"] if r else None
    return cid or None


def draft_tags(draft: dict, image_ids: list[str]) -> list[str] | None:
    """Теги картинок: уверенные подсказки теггера + то, что вы сами отметили для e6ai. None — теггер ещё не смотрел."""
    tags, known = set(), False
    for fid in image_ids:
        f = sub("uploads") / f"{fid}.tags.json"
        if f.exists():
            known = True
            try:
                tags |= {t for t, p in json.loads(f.read_text(encoding="utf-8")) if p >= M.TAG_PROB}
            except Exception:
                pass
    for lst in (draft.get("e6ai_tags") or {}).values():
        if lst:
            known = True
            tags |= set(lst)
    return sorted(tags - M.NOISE_TAGS) if known else None


def for_draft(draft: dict, when: datetime | None = None) -> dict:
    from app.publish import runner
    from app.publish import settings as PS
    try:
        files = runner._files(draft)
    except ValueError:
        files = []
    if not files:
        return {"items": [], "why": "Добавьте картинки — прогноз считается для конкретного поста"}
    p = runner.plan(draft)
    cfg = PS.load()["telegram"]
    imgs = [f for f in files if f["kind"] == "image"]
    kind = "anim" if any(f["kind"] in ("gif", "video") for f in files) else ("album" if len(imgs) > 1 else "single")
    tags = draft_tags(draft, [f["id"] for f in imgs])
    items = []
    for d in p["dests"]:
        if not d["key"].startswith("telegram:") or d["key"] == "telegram:group" or not d["will"]:
            continue
        ch = cfg["channels"][int(d["key"].split(":")[1])]
        cid = _db_chat_id(ch)
        if not cid:
            items.append({"ok": False, "key": d["key"], "channel": ch.get("title") or ch.get("id"),
                          "why": "Канал не найден в статистике — импортируйте его в «Импорт для статистики»"})
            continue
        f = M.forecast(chat_id=cid, when=when, kind=kind, n_media=max(len(files), 1), rating=draft.get("rating") or "sfw",
                       text=draft.get("text") or "", tags=tags)
        f["key"] = d["key"]
        f.setdefault("channel", ch.get("title") or ch.get("id"))
        items.append(f)
    return {"items": items, "tags_known": tags is not None, "n_tags": len(tags or [])}
