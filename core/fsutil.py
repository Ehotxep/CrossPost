"""Безопасная работа с файлами настроек: запись не должна оставлять битый файл, а чтение — ронять программу.

atomic_write: пишет во временный файл рядом, сбрасывает на диск и подменяет им целевой одним действием;
прежняя версия остаётся как «<имя>.bak». Обрыв питания или процесса посреди записи не портит файл.
read_json: если файл повреждён — битый сохраняется как «<имя>.corrupt-<время>», а читается «.bak»
(иначе значение по умолчанию), и программа запускается, а не падает.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def atomic_write(path, text: str, backup: bool = True) -> None:
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        if backup and p.exists():
            try:
                shutil.copy2(p, p.with_name(p.name + ".bak"))
            except OSError:
                pass
        os.replace(tmp, p)


def read_json(path, default=None, expect=dict):
    """Читает JSON. Битый/пустой/не того типа файл не роняет программу: берём .bak или default."""
    p = Path(path)
    if not p.exists():
        return default() if callable(default) else (default if default is not None else expect())
    for cand in (p, p.with_name(p.name + ".bak")):
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(data, expect):
                if cand is not p:   # основной был повреждён — вернули копию, чиним основной
                    _quarantine(p)
                    try:
                        shutil.copy2(cand, p)
                    except OSError:
                        pass
                return data
        except (OSError, ValueError):
            continue
    _quarantine(p)
    return default() if callable(default) else (default if default is not None else expect())


def _quarantine(p: Path) -> None:
    try:
        if p.exists():
            shutil.move(str(p), str(p.with_name(f"{p.name}.corrupt-{int(time.time())}")))
            from app.applog import log
            log(f"файл {p.name} был повреждён — сохранён как .corrupt, взята резервная копия", exc=False)
    except Exception:  # noqa: BLE001
        pass
