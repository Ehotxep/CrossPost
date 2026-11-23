"""Edit only Telegram messages recorded in publication history; never resend media."""
import hashlib
import html
import json
import re

from app import db
from app.paths import sub
from app.publish import settings, tg_bot
from core.fsutil import file_lock


def _record(pub_id, key):
    rows = db.query("SELECT * FROM publications WHERE id=?", (int(pub_id),))
    if not rows:
        raise ValueError("Публикация не найдена")
    pub = rows[0]
    dests = json.loads(pub["dests"] or "[]")
    dest = next((d for d in dests if d.get("key") == key), None)
    if not dest or dest.get("platform") != "telegram" or key == "telegram:group" or not dest.get("message_ids") or not dest.get("chat_id"):
        raise ValueError("Нет сохранённого сообщения Telegram для редактирования")
    return pub, dests, dest


def _revision(dest):
    return hashlib.sha256(json.dumps(dest, sort_keys=True).encode()).hexdigest()


def prepare(pub_id, key):
    pub, _, dest = _record(pub_id, key)
    exact = "edit_text" in dest
    text = dest.get("edit_text") if exact else "\n\n".join(html.escape(s) for s in (pub.get("title"), pub.get("text")) if s)
    return {"pub_id": int(pub_id), "key": key, "name": dest.get("name", key),
            "text": text, "kind": dest.get("edit_kind", "caption"),
            "exact": exact, "revision": _revision(dest), "link": dest.get("link", "")}


def apply(pub_id, key, text, revision):
    if not isinstance(text, str):
        raise ValueError("Нужен текст сообщения")
    with file_lock(sub("locks") / f"edit-post-{int(pub_id)}.lock"):
        pub, _, dest = _record(pub_id, key)
        if revision != _revision(dest):
            raise ValueError("Запись изменилась. Откройте редактор заново.")
        kind = dest.get("edit_kind", "caption")
        visible = html.unescape(re.sub(r"<[^>]*>", "", text))
        limit = 4096 if kind == "text" else 1024
        if len(visible.encode("utf-16-le")) // 2 > limit or (kind == "text" and not visible.strip()):
            raise ValueError(f"Текст должен содержать не более {limit} символов" + (" и не быть пустым" if kind == "text" else ""))
        cfg = settings.load()["telegram"]
        token = cfg.get("bot_token") or ""
        if not token:
            raise ValueError("Настройте бота Telegram")
        if dest.get("edit_bot_id") and dest["edit_bot_id"] != token.split(":")[0]:
            raise ValueError("Этот пост отправлен другим ботом. Подключите исходного бота.")
        message_id = dest.get("edit_message_id") or dest["message_ids"][0]
        if cfg.get("transport") == "mtproto":
            tg_bot._mt(token).edit_text(dest["chat_id"], message_id, text)
        else:
            try:
                tg_bot.call(token, "editMessageText" if kind == "text" else "editMessageCaption",
                            {"chat_id": dest["chat_id"], "message_id": int(message_id),
                             "text" if kind == "text" else "caption": text, "parse_mode": "HTML"})
            except Exception as error:
                if "message is not modified" not in str(error).lower():
                    raise
        # Read again so unrelated destination updates are preserved.
        with db.tx() as c:
            raw = c.execute("SELECT dests FROM publications WHERE id=?", (int(pub_id),)).fetchone()
            dests = json.loads(raw["dests"])
            target = next(d for d in dests if d.get("key") == key)
            target.update(edit_text=text, edit_kind=kind, edit_message_id=message_id, edit_bot_id=token.split(":")[0])
            c.execute("UPDATE publications SET dests=? WHERE id=?", (json.dumps(dests, ensure_ascii=False), int(pub_id)))
        return prepare(pub_id, key)
