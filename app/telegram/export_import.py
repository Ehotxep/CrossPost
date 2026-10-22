"""Импорт канала из выгрузки Telegram Desktop («Экспорт истории чата», формат JSON).

Работает без api_id и без входа в аккаунт. Ограничения выгрузки:
- просмотров в ней нет — только реакции, поэтому успех таких каналов считается по реакциям;
- альбомы не помечены — склеиваем подряд идущие сообщения с одинаковым временем отправки;
- если в выгрузку включены фото, из них делаются превью.

Повторный импорт свежей выгрузки не создаёт дублей, а обновляет реакции.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from app import db
from app.paths import sub
from app.telegram.service import _now, save_messages

MEDIA = {"video_file": "video", "animation": "gif", "sticker": "sticker", "voice_message": "audio",
         "audio_file": "audio", "video_message": "video"}
PREVIEW_SIZE = 640


def find_result(path: str) -> Path:
    p = Path(str(path).strip().strip('"'))
    if p.is_dir():
        p = p / "result.json"
    if not p.is_file():
        raise FileNotFoundError(f"Не найден файл выгрузки: {p}")
    return p


def _text(t) -> str:
    if isinstance(t, str):
        return t
    return "".join(x if isinstance(x, str) else x.get("text", "") for x in t or [])


def _reactions(m) -> int:
    return sum(int(r.get("count", 0)) for r in m.get("reactions") or [])


def _media_type(m) -> str:
    if "photo" in m:
        return "photo"
    if m.get("media_type"):
        return MEDIA.get(m["media_type"], "document")
    if "file" in m:
        return "document"
    return "text"


def _file(base: Path, rel) -> Path | None:
    if not rel or not isinstance(rel, str) or rel.startswith("("):  # «(File not included…)»
        return None
    f = (base / rel).resolve()
    return f if f.is_file() else None


def _make_preview(src: Path, name: str) -> str | None:
    from PIL import Image
    out = sub("previews") / name
    if not out.exists():
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
                im.save(out, "JPEG", quality=85)
        except Exception:
            return None
    return out.name


def _is_own_forward(m, own_ids: set, own_names: list) -> bool:
    """Пересылки из ваших же каналов (например, SFW-копия поста из 18+) — это ваш арт, их считаем."""
    fid = str(m.get("forwarded_from_id") or "")
    if fid.startswith("channel") and "-100" + fid[len("channel"):] in own_ids:
        return True
    name = (m.get("forwarded_from") or "").lower()
    return any(n and n in name for n in own_names)


def parse(path: str):
    """Читает выгрузку → (сведения о канале, список сообщений в формате импорта)."""
    f = find_result(path)
    with open(f, encoding="utf-8") as fh:
        data = json.load(fh)
    if "messages" not in data:
        if "chats" in data:
            raise ValueError("Это выгрузка всего аккаунта. Нужен экспорт одного канала: меню канала → «Экспорт истории чата».")
        raise ValueError("Файл не похож на выгрузку Telegram Desktop (нет списка сообщений).")
    if data.get("type") not in ("public_channel", "private_channel", None):
        raise ValueError("Это выгрузка не канала, а чата или группы. Нужен экспорт канала.")
    chat_id = "-100" + str(data.get("id"))
    ch = {"chat_id": chat_id, "title": data.get("name") or chat_id, "username": None}

    known = db.query("SELECT chat_id, title FROM channels WHERE platform='telegram'")
    own_ids = {r["chat_id"] for r in known} | {chat_id}
    own_names = sorted({(r["title"] or "").lower() for r in known} | {ch["title"].lower()}, key=len, reverse=True)

    msgs, prev, album = [], None, None
    for m in data["messages"]:
        if m.get("type") != "message":
            prev = None
            continue
        ts = int(m["date_unixtime"])
        fwd_key = m.get("forwarded_from_id")
        media = _media_type(m)
        # альбом: подряд идущие сообщения с медиа, отправленные в одну и ту же секунду
        if prev and media != "text" and prev["media_type"] != "text" and prev["_ts"] == ts and prev["_fwd"] == fwd_key:
            if album is None:
                album = -prev["msg_id"]
                prev["grouped_id"] = album
            gid = album
        else:
            gid, album = None, None
        item = {
            "msg_id": int(m["id"]), "date": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            "text": _text(m.get("text")), "grouped_id": gid, "media_type": media,
            "views": None, "forwards": None, "replies": None, "reactions": _reactions(m),
            "is_forward": bool(m.get("forwarded_from")) and not _is_own_forward(m, own_ids, own_names),
            "_ts": ts, "_fwd": fwd_key,
            "_img": _file(f.parent, m.get("photo")) or _file(f.parent, m.get("thumbnail")),
        }
        msgs.append(item)
        prev = item
    return ch, msgs


def import_export(path: str, report=lambda **k: None) -> dict:
    report(progress=0.02, message="Читаю выгрузку…")
    ch, msgs = parse(path)
    if not msgs:
        raise ValueError("В выгрузке нет сообщений.")
    with db.tx() as c:
        existed = c.execute("SELECT 1 FROM channels WHERE platform='telegram' AND chat_id=?", (ch["chat_id"],)).fetchone()
        guess = "nsfw" if any(k in ch["title"].lower() for k in ("18+", "nsfw", "18 +")) else "sfw"
        c.execute("""INSERT INTO channels (platform, chat_id, title, rating, selected) VALUES ('telegram', ?, ?, ?, 1)
                     ON CONFLICT(platform, chat_id) DO UPDATE SET title=excluded.title""",
                  (ch["chat_id"], ch["title"], guess))
    n_prev = 0
    for k, m in enumerate(msgs):
        img = m.pop("_img")
        m.pop("_ts"), m.pop("_fwd")
        if img is not None:
            name = _make_preview(img, f"tg_{ch['chat_id']}_{m['msg_id']}.jpg")
            if name:
                m["preview"] = name
                n_prev += 1
        if k % 50 == 0:
            report(progress=0.05 + 0.75 * k / len(msgs), message=f"«{ch['title']}»: сообщение {k + 1} из {len(msgs)}")
    report(progress=0.85, message=f"«{ch['title']}»: сохраняю…")
    save_messages(ch, msgs, snapshot=bool(existed))
    with db.tx() as c:   # last_msg_id не трогаем: если позже подключите API, он дозагрузит просмотры
        c.execute("UPDATE channels SET synced_at=? WHERE platform='telegram' AND chat_id=?", (_now(), ch["chat_id"]))
    posts = db.query("SELECT COUNT(*) AS n FROM posts WHERE platform='telegram' AND chat_id=?", (ch["chat_id"],))[0]["n"]
    n_prev = db.query("SELECT COUNT(*) AS n FROM posts WHERE platform='telegram' AND chat_id=? AND preview IS NOT NULL",
                      (ch["chat_id"],))[0]["n"] if n_prev else 0
    msg = f"«{ch['title']}»: {posts} постов" + (f", с превью: {n_prev}" if n_prev else ", без превью (фото не были включены в выгрузку)")
    report(progress=1.0, message=msg)
    return {"chat_id": ch["chat_id"], "title": ch["title"], "posts": posts, "previews": n_prev, "updated": bool(existed)}
