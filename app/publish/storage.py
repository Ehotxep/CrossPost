"""Миниатюры публикаций и уборка тяжёлых копий.

При добавлении в пост файл копируется в папку данных (uploads/<id>/), поэтому перемещение исходников
у вас на диске ничего не ломает. Но полноразмерные копии занимают много места, поэтому:
  • у каждой публикации — своя постоянная миниатюра (thumbs/pub_<id>.jpg, ~512 px):
    история, календарь и статистика берут картинку только оттуда;
  • тяжёлые копии оригиналов удаляются через 7 дней после публикации (лёгкие версии и миниатюры остаются);
  • файлы запланированных постов хранятся до выхода;
  • забытые файлы, которые так и не ушли в пост, удаляются через 30 дней.
"""
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import db
from app.paths import sub

KEEP_DAYS = 7
ORPHAN_DAYS = 30
THUMB = 512


def pub_thumb_path(pub_id: int) -> Path:
    return sub("thumbs") / f"pub_{int(pub_id)}.jpg"


def _thumb_from(src: Path, dst: Path, size: int = THUMB) -> bool:
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            tmp = dst.with_name(dst.name + ".tmp")
            im.convert("RGB").save(tmp, "JPEG", quality=84)
            tmp.replace(dst)
        return True
    except Exception:
        return False


def make_pub_thumb(pub_id: int, files: list) -> bool:
    """Постоянная миниатюра публикации — по первой картинке (или первому кадру GIF)."""
    from app.publish import uploads
    dst = pub_thumb_path(pub_id)
    if dst.exists():
        return True
    for f in files or []:
        fid = f["id"] if isinstance(f, dict) else f
        try:
            src = uploads.still(fid)   # у видео — кадр из него
            if src and _thumb_from(src, dst):
                return True
        except ValueError:
            pass
        small = sub("uploads") / f"{fid}.thumb.jpg"   # оригинала уже нет — хотя бы маленькая
        if small.exists():
            shutil.copyfile(small, dst)
            return True
    return False


def thumb_for(pub_id: int) -> Path:
    """Миниатюра публикации; если её ещё нет — сделать сейчас (для старых публикаций)."""
    dst = pub_thumb_path(pub_id)
    if not dst.exists():
        import app.publish.runner  # noqa: F401
        r = db.query("SELECT files FROM publications WHERE id=?", (int(pub_id),))
        if r:
            make_pub_thumb(int(pub_id), json.loads(r[0]["files"] or "[]"))
    return dst


def big_for(pub_id: int) -> Path:
    """Крупное превью публикации (до 1400 px) для шторки статистики. Оригинала уже нет — берём миниатюру."""
    from app.publish import uploads
    dst = sub("thumbs") / f"big_{int(pub_id)}.jpg"
    if dst.exists():
        return dst
    import app.publish.runner  # noqa: F401
    r = db.query("SELECT files FROM publications WHERE id=?", (int(pub_id),))
    for f in (json.loads(r[0]["files"] or "[]") if r else []):
        try:
            src = uploads.still(f["id"] if isinstance(f, dict) else f)
        except ValueError:
            src = None
        if src and _thumb_from(src, dst, 1400):
            return dst
    return thumb_for(pub_id)


def ensure_pub_thumbs() -> int:
    import app.publish.runner  # noqa: F401
    n = 0
    for r in db.query("SELECT id, files FROM publications"):
        if not pub_thumb_path(r["id"]).exists():
            n += make_pub_thumb(r["id"], json.loads(r["files"] or "[]"))
    return n


def _scheduled_ids() -> set:
    from app.publish import schedule  # noqa: F401  (таблица scheduled)
    out = set()
    for r in db.query("SELECT draft FROM scheduled WHERE state IN ('pending','running','missed')"):
        try:
            out |= set(json.loads(r["draft"] or "{}").get("files") or [])
        except Exception:
            pass
    return out


def used_ids() -> set:
    """Файлы, которые нужны опубликованным (для досылки) и запланированным постам."""
    import app.publish.runner  # noqa: F401
    ids = set(_scheduled_ids())
    for r in db.query("SELECT files FROM publications"):
        for f in json.loads(r["files"] or "[]"):
            ids.add(f["id"] if isinstance(f, dict) else f)
    return ids


def _dir_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def cleanup(days: int = KEEP_DAYS, everything_published: bool = False) -> dict:
    """Удалить тяжёлые копии опубликованных постов старше days дней. Возвращает {"files", "bytes"}."""
    import app.publish.runner  # noqa: F401
    keep = _scheduled_ids()
    edge = datetime.now(timezone.utc) - timedelta(days=days)
    published = set()
    freed, n = 0, 0
    for r in db.query("SELECT id, created, files FROM publications"):
        files = json.loads(r["files"] or "[]")
        ids = [f["id"] if isinstance(f, dict) else f for f in files]
        published |= set(ids)
        try:
            created = datetime.fromisoformat(r["created"].replace("Z", "+00:00"))
        except Exception:
            continue
        if not everything_published and created > edge:
            continue
        make_pub_thumb(r["id"], files)   # сначала миниатюра — потом можно удалять
        try:
            big_for(r["id"])   # и крупное превью для шторки статистики: без оригинала его уже не сделать
        except Exception:
            pass
        for fid in ids:
            d = sub("uploads") / str(fid)
            if fid in keep or not d.is_dir():
                continue
            try:   # маленькая миниатюра файла тоже остаётся
                from app.publish import uploads
                uploads.thumb(fid)
            except Exception:
                pass
            freed += _dir_size(d) + sum(x.stat().st_size for x in sub("uploads").glob(f"{fid}.conv-*"))
            shutil.rmtree(d, ignore_errors=True)
            (sub("uploads") / f"{fid}.big.jpg").unlink(missing_ok=True)   # просмотр на экране поста больше не нужен
            from app.publish import media
            media.remove(fid)   # переделанные под площадки видео
            n += 1
    # забытые файлы: добавлены в пост, но так и не опубликованы и не запланированы
    old = time.time() - ORPHAN_DAYS * 86400
    for d in sub("uploads").iterdir():
        if d.is_dir() and d.name not in published and d.name not in keep:
            try:
                if d.stat().st_mtime < old:
                    freed += _dir_size(d)
                    from app.publish import uploads
                    uploads.remove(d.name)
                    n += 1
            except OSError:
                pass
    return {"files": n, "bytes": freed}


def usage() -> dict:
    up = sub("uploads")
    originals = sum(_dir_size(d) for d in up.iterdir() if d.is_dir()) + sum(f.stat().st_size for f in up.glob("*.conv-*"))
    light = sum(f.stat().st_size for f in up.glob("*.light-*.jpg"))
    small = sum(f.stat().st_size for f in up.glob("*.thumb.jpg")) + _dir_size(sub("thumbs"))
    try:
        from app.publish import outputs
        ready = outputs.usage()["bytes"]
    except Exception:   # noqa: BLE001
        ready = 0
    return {"originals": originals, "light": light, "thumbs": small, "previews": _dir_size(sub("previews")),
            "outputs": ready, "keep_days": KEEP_DAYS}


_last = {"t": 0.0}


def maintain(min_gap: float = 6 * 3600) -> dict | None:
    """Раз в несколько часов: миниатюры для старых публикаций и уборка копий."""
    if time.time() - _last["t"] < min_gap:
        return None
    _last["t"] = time.time()
    ensure_pub_thumbs()
    return cleanup()
