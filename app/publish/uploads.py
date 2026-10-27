"""Файлы, добавленные в новый пост (перетаскиванием, вставкой или выбором).

Интерфейс присылает файл целиком, мы кладём его в папку uploads/<id>/ и описываем:
картинка / GIF / видео, размеры, вес. Оригинал не меняется.
"""
import json
import re
import secrets
import shutil
from pathlib import Path

from app.paths import sub

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".webm", ".mov"}
MAX_BYTES = 500 * 1024 * 1024


def _safe(name: str) -> str:
    name = Path(name or "file").name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ") or "file"
    return name[:120]


def describe(path: Path) -> dict:
    ext = path.suffix.lower()
    info = {"name": path.name, "size": path.stat().st_size, "kind": None, "width": None, "height": None, "format": ext.lstrip(".").upper()}
    if ext in VIDEO_EXT:
        info["kind"] = "video"
        pr = probe_cached(path)
        if pr:
            info.update(width=pr.get("width"), height=pr.get("height"), duration=pr.get("duration"), vcodec=pr.get("vcodec"))
        return info
    from PIL import Image
    try:
        with Image.open(path) as im:
            info["width"], info["height"] = im.size
            info["format"] = im.format
            if im.format == "GIF" or getattr(im, "is_animated", False):
                info["kind"] = "gif"
                try:
                    info["frames"] = getattr(im, "n_frames", 1)
                except Exception:
                    pass
            else:
                info["kind"] = "image"
    except Exception:
        raise ValueError(f"{path.name}: не похоже на картинку или видео (поддерживаются PNG, JPEG, WEBP, GIF, MP4)")
    return info


def probe_cached(path: Path) -> dict:
    """Разбор видео ffprobe — один раз, дальше из файла рядом (план поста пересчитывается часто)."""
    uid = path.parent.name
    side = sub("uploads") / f"{uid}.probe.json"
    if side.exists():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except ValueError:
            pass
    from app.publish import media
    pr = media.probe(path)
    if pr:
        side.write_text(json.dumps(pr), encoding="utf-8")
    return pr


def still(uid: str) -> Path | None:
    """Картинка для миниатюры и теггера: сам файл, а у видео — кадр из него (нужен ffmpeg)."""
    p = path_of(uid)
    if p.suffix.lower() not in VIDEO_EXT:
        return p
    fr = sub("uploads") / f"{uid}.frame.jpg"
    if fr.exists():
        return fr
    from app.publish import media
    return fr if media.frame(p, fr) else None


def save(name: str, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Файл больше 500 МБ")
    uid = secrets.token_hex(8)
    folder = sub("uploads") / uid
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / _safe(name)
    p.write_bytes(data)
    return _finish(uid, folder, p)


def save_stream(name: str, src, length: int) -> dict:
    """То же, но тело запроса пишется на диск кусками — большой файл не лежит целиком (и дважды) в памяти."""
    if length > MAX_BYTES:
        raise ValueError("Файл больше 500 МБ")
    uid = secrets.token_hex(8)
    folder = sub("uploads") / uid
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / _safe(name)
    try:
        left = length
        with open(p, "wb") as f:
            while left > 0:
                chunk = src.read(min(left, 1 << 20))
                if not chunk:
                    raise ValueError("загрузка файла оборвалась — добавьте его ещё раз")
                f.write(chunk)
                left -= len(chunk)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return _finish(uid, folder, p)


def _finish(uid: str, folder: Path, p: Path) -> dict:
    try:
        info = describe(p)
    except ValueError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if info["kind"] == "image":   # лёгкую версию начинаем готовить сразу, пока пишется текст
        from app.publish import prep, settings
        prep.ensure_async(uid, settings.load()["light"])
    return dict(info, id=uid)


def new_slot(name: str) -> tuple[str, Path]:
    """Пустое место под файл, который делаем сами (например GIF из видео): (id, путь)."""
    uid = secrets.token_hex(8)
    folder = sub("uploads") / uid
    folder.mkdir(parents=True, exist_ok=True)
    return uid, folder / _safe(name)


def path_of(uid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", uid or ""):
        raise ValueError("неверный id файла")
    folder = sub("uploads") / uid
    files = [f for f in folder.iterdir() if f.is_file()] if folder.exists() else []
    if not files:
        raise ValueError("файл больше не найден — добавьте его заново")
    return files[0]


def remove(uid: str):
    try:
        p = path_of(uid)
        shutil.rmtree(p.parent, ignore_errors=True)
        (p.parent.parent / f"{uid}.thumb.jpg").unlink(missing_ok=True)
        (p.parent.parent / f"{uid}.big.jpg").unlink(missing_ok=True)
        (p.parent.parent / f"{uid}.tags.json").unlink(missing_ok=True)
        (p.parent.parent / f"{uid}.probe.json").unlink(missing_ok=True)
        (p.parent.parent / f"{uid}.strip.jpg").unlink(missing_ok=True)
        (p.parent.parent / f"{uid}.looprev.webm").unlink(missing_ok=True)
        from app.publish import media
        media.remove(uid)
        from app.publish import prep
        prep.remove(uid)
    except ValueError:
        pass


def big(uid: str, size: int = 1800) -> bytes:
    """Крупная картинка для просмотра в приложении (до 1800 px, кеш рядом с файлом). Нет оригинала — миниатюра."""
    if not re.fullmatch(r"[0-9a-f]{16}", uid or ""):
        raise ValueError("неверный id файла")
    b = sub("uploads") / f"{uid}.big.jpg"
    if b.exists():
        return b.read_bytes()
    try:
        src = still(uid)
    except (ValueError, FileNotFoundError, OSError):
        src = None
    if src is None or not Path(src).exists():
        return thumb(uid)
    from PIL import Image, ImageOps
    with Image.open(src) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((size, size))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im.convert("RGB").save(b, "JPEG", quality=90)
    return b.read_bytes()


def thumb(uid: str, size: int = 360) -> bytes:
    """Миниатюра для интерфейса (кешируется рядом с папкой файла)."""
    if not re.fullmatch(r"[0-9a-f]{16}", uid or ""):
        raise ValueError("неверный id файла")
    t = sub("uploads") / f"{uid}.thumb.jpg"
    if t.exists():   # оригинал мог быть уже убран (через 7 дней после публикации) — миниатюра остаётся
        return t.read_bytes()
    src = still(uid)
    if src is None:
        raise ValueError("нет миниатюры видео — нужен ffmpeg")
    if not t.exists():
        from PIL import Image, ImageOps
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            im.convert("RGB").save(t, "JPEG", quality=85)
    return t.read_bytes()
