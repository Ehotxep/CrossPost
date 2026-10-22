"""Готовые файлы: JPEG-превью, GIF и видео, которые программа сделала сама, — копией в понятной папке.

По умолчанию это папка «Файлы» в каталоге программы (если туда нельзя писать — «Изображения\\Crosspost»),
в ней три подпапки: «JPEG-превью», «GIF», «Видео». Можно указать свою папку и раскладывать по датам.
Файлы здесь не удаляются автоматически — это ваша библиотека. Рабочие копии в uploads (кеш) по-прежнему чистятся.

Каждый файл сохраняется один раз: что сохранено, помнит outputs.json в папке данных. Удалили файл руками —
заново он не появится. Копии, а не ссылки: правка файла в графическом редакторе не должна менять то, что уйдёт в пост.
Любая ошибка записи тут не должна мешать публикации — save() ничего не бросает.
"""
import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path

from app.paths import app_dir, data_dir

KINDS = {"jpeg": "JPEG-превью", "gif": "GIF", "video": "Видео"}
FOLDER_NAME = "Файлы"
_lock = threading.RLock()


def cfg() -> dict:
    from app.publish import settings
    c = dict(settings.load().get("outputs") or {})
    return {"dir": (c.get("dir") or "").strip(), "by_date": bool(c.get("by_date")),
            **{k: c.get(k, True) is not False for k in KINDS}}


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".crosspost_test"
        t.write_bytes(b"")
        t.unlink()
        return True
    except OSError:
        return False


def default_root() -> Path:
    """Каталог программы\\Файлы (при запуске с CROSSPOST_DATA — рядом с данными: так живут тесты и переносимые копии)."""
    if os.environ.get("CROSSPOST_DATA"):
        return data_dir() / FOLDER_NAME
    return app_dir() / FOLDER_NAME


def root() -> tuple[Path, str]:
    """(папка, откуда): custom — своя, default — по умолчанию, fallback — запасная, раз в основную писать нельзя."""
    c = cfg()
    if c["dir"] and _writable(Path(c["dir"]).expanduser()):
        return Path(c["dir"]).expanduser(), "custom"
    d = default_root()
    if _writable(d):
        return d, "default" if not c["dir"] else "default-fallback"
    alt = Path.home() / "Pictures" / "Crosspost"
    if _writable(alt):
        return alt, "fallback"
    return data_dir() / FOLDER_NAME, "fallback"


# --- что уже сохранено ---------------------------------------------------------------------

def _index_path() -> Path:
    return data_dir() / "outputs.json"


def _load() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8")).get("saved", {})
    except (OSError, ValueError):
        return {}


def _store(idx: dict):
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps({"saved": idx}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_index_path())


def _unique(folder: Path, name: str) -> Path:
    dst = folder / name
    stem, suf, n = dst.stem, dst.suffix, 2
    while dst.exists():
        dst = folder / f"{stem} ({n}){suf}"
        n += 1
    return dst


def save(kind: str, src, name: str, key: str) -> Path | None:
    """Положить копию готового файла в папку. key — что именно сохраняем (одно и то же — один раз).
    Возвращает путь копии или None (вид отключён, файл уже сохранялся и удалён руками, ошибка записи)."""
    try:
        c = cfg()
        src = Path(src)
        if kind not in KINDS or not c[kind] or not src.is_file():
            return None
        with _lock:
            idx = _load()
            if key in idx:
                p = Path(idx[key])
                return p if p.exists() else None
            base, _ = root()
            folder = base / KINDS[kind]
            if c["by_date"]:
                folder = folder / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True, exist_ok=True)
            dst = _unique(folder, name)
            part = dst.with_name(dst.name + ".part")
            shutil.copyfile(src, part)
            part.replace(dst)
            idx[key] = str(dst)
            _store(idx)
            return dst
    except Exception as e:  # noqa: BLE001 — запись в папку не должна ломать публикацию
        print(f"outputs: не удалось сохранить {name}: {e}")
        return None


# --- сведения для настроек -----------------------------------------------------------------

def usage() -> dict:
    base, _ = root()
    out = {"bytes": 0, "files": 0, "by_kind": {}}
    for k, folder in KINDS.items():
        n = b = 0
        d = base / folder
        if d.is_dir():
            for f in d.rglob("*"):
                if f.is_file() and not f.name.endswith(".part"):
                    n += 1
                    try:
                        b += f.stat().st_size
                    except OSError:
                        pass
        out["by_kind"][k] = {"files": n, "bytes": b}
        out["files"] += n
        out["bytes"] += b
    return out


def info() -> dict:
    c = cfg()
    base, how = root()
    return {"dir": str(base), "how": how, "default": str(default_root()), "custom": bool(c["dir"]),
            "by_date": c["by_date"], **{k: c[k] for k in KINDS}, "folders": KINDS, "usage": usage()}


def _inside(a: Path, b: Path) -> bool:
    try:
        a.resolve().relative_to(b.resolve())
        return True
    except ValueError:
        return False


def apply(folder: str, by_date: bool, kinds: dict, move: bool) -> dict:
    """Сменить папку и виды файлов. folder пустой — по умолчанию. move — перенести уже сохранённое в новую папку."""
    from app.publish import settings as PS
    folder = (folder or "").strip().strip('"')
    if folder:
        p = Path(folder).expanduser()
        if not p.is_absolute():
            raise ValueError("Укажите полный путь к папке, например D:\\Арт\\Готовые файлы")
        if not _writable(p):
            raise ValueError("В эту папку нельзя записывать — выберите другую")
        new = p
    else:
        new = default_root()
        if not _writable(new):
            raise ValueError("В папку по умолчанию записывать нельзя — укажите свою")
    with _lock:
        old, _ = root()
        if move and old.resolve() != new.resolve():
            if _inside(new, old) or _inside(old, new):
                raise ValueError("Новая папка не должна лежать внутри старой, и наоборот")
        PS.update("outputs", {"dir": str(new) if folder else "", "by_date": bool(by_date), **{k: bool(kinds.get(k, True)) for k in KINDS}})
        moved = _move_all(old, new) if move and old.resolve() != new.resolve() else 0
    out = info()
    out["moved"] = moved
    return out


def _move_all(old: Path, new: Path) -> int:
    """Перенести три подпапки из старой папки в новую, не затирая существующее; поправить память о сохранённом."""
    idx = _load()
    n = 0
    for folder in KINDS.values():
        src = old / folder
        if not src.is_dir():
            continue
        for f in sorted(src.rglob("*")):
            if not f.is_file() or f.name.endswith(".part"):
                continue
            rel = f.relative_to(old)
            dst = new / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst = _unique(dst.parent, dst.name)
            shutil.move(str(f), str(dst))
            for k, v in idx.items():
                if v == str(f):
                    idx[k] = str(dst)
            n += 1
        for d in sorted((x for x in src.rglob("*") if x.is_dir()), reverse=True):   # опустевшие подпапки (даты) убираем
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass
    _store(idx)
    return n


def open_folder() -> str:
    import subprocess
    import sys
    base, _ = root()
    base.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(base))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(base)])
    return str(base)
