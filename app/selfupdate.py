"""Обновление программы по кнопке: скачать выпуск с GitHub → проверить → заменить файлы → перезапустить.

Два шага, чтобы человек сам выбрал момент перезапуска:
  prepare()  — скачивание, проверка контрольной суммы, распаковка, проверка файлов (окно работает как обычно);
  apply()    — остановка фоновой отправки, запуск отдельного процесса apply_update.py и закрытие окна.
Замену файлов делает apply_update.py: он держит резервную копию и откатывает всё, если новая версия не запустилась.
Скачиваем только с https://github.com/<репозиторий>/releases/download/…, файл обязан иметь контрольную сумму SHA-256.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from app import __version__, updates
from app.paths import app_dir, data_dir

DL_PREFIX = f"https://github.com/{updates.REPO}/releases/download/"
MAX_ZIP = 200 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024
MAX_FILES = 6000


def _upd() -> Path:
    p = data_dir() / "updates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def can_update() -> tuple[bool, str]:
    """(можно ли обновлять отсюда, почему нельзя)."""
    root = app_dir()
    if (root / ".git").exists():
        return False, "это рабочая копия из git — обновляйтесь командой git pull"
    for d in (root, root / "app"):
        try:
            t = d / ".write-test"
            t.write_text("x")
            t.unlink()
        except OSError:
            return False, "в папку программы нельзя записывать (она в «Program Files»?) — перенесите её, например, в Документы"
    return True, ""


def ready() -> dict | None:
    """Скачанная и проверенная версия, ждущая установки."""
    try:
        d = json.loads((_upd() / "ready.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not Path(d.get("root", "")).joinpath("app", "__init__.py").exists() or not updates.newer(d.get("version", "")):
        return None
    return d


def result(ack: bool = False) -> dict | None:
    """Итог прошлого обновления (покажем при запуске один раз)."""
    p = data_dir() / "update-result.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if ack:
        p.unlink(missing_ok=True)
    return d


# ---------------------------------------------------------------- скачивание и проверка

def _release() -> dict:
    """Описание последнего выпуска вместе с файлами (для проверки уведомлений нужна только версия)."""
    import requests
    r = requests.get(updates.API, timeout=15, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Crosspost/{__version__}"})
    if r.status_code == 404:
        raise ValueError("выпусков пока нет или репозиторий закрыт")
    r.raise_for_status()
    return r.json()


def _download(url: str, dest: Path, report, label: str, limit: int):
    import requests
    if not url.startswith(DL_PREFIX):
        raise ValueError("адрес файла не из репозитория Crosspost — отказываюсь качать")
    import time
    got, attempt = 0, 0
    dest.unlink(missing_ok=True)
    while True:   # обрыв связи — докачиваем с того же места (Range); сколько угодно попыток, останавливает только «Отмена»
        try:
            hdr = {"User-Agent": f"Crosspost/{__version__}", **({"Range": f"bytes={got}-"} if got else {})}
            with requests.get(url, stream=True, timeout=30, headers=hdr) as r:
                r.raise_for_status()
                if got and r.status_code != 206:   # сервер не умеет докачку — начинаем сначала
                    got = 0
                total = got + int(r.headers.get("Content-Length") or 0)
                if total > limit:
                    raise ValueError("файл слишком большой")
                with open(dest, "ab" if got else "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        got += len(chunk)
                        if got > limit:
                            raise ValueError("файл слишком большой")
                        f.write(chunk)
                        report(0.05 + 0.6 * got / total if total else None,
                               f"{label}: {got // (1 << 20)} из {total // (1 << 20)} МБ" if total else None)
                if total and got < total:
                    raise requests.ConnectionError("соединение закрылось раньше времени")
                return
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            attempt += 1
            for left in range(min(30, 2 * attempt), 0, -1):
                report(message=f"{label}: обрыв связи, продолжу с {got // (1 << 20)} МБ через {left} с (попытка {attempt + 1})")
                time.sleep(1)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _unpack(zpath: Path, dest: Path):
    """Распаковка с защитой от «../» в путях и от архивов-бомб."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    base = dest.resolve()
    with zipfile.ZipFile(zpath) as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ValueError("архив выглядит подозрительно большим")
        for i in infos:
            target = (dest / i.filename).resolve()
            if base != target and base not in target.parents:
                raise ValueError("в архиве есть файл вне папки — отказываюсь распаковывать")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("в архиве есть символическая ссылка — отказываюсь распаковывать")
        z.extractall(dest)


def _find_root(dest: Path) -> Path:
    for p in [dest, *sorted(dest.iterdir())]:
        if p.is_dir() and (p / "app" / "__init__.py").exists():
            return p
    raise ValueError("в архиве нет папки app — это не выпуск Crosspost")


def _check_tree(root: Path, want: str) -> str:
    init = (root / "app" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init)
    ver = m.group(1) if m else ""
    if want and ver != want:
        raise ValueError(f"версия внутри архива ({ver or '?'}) не совпадает с выпуском ({want})")
    if not updates.newer(ver):
        raise ValueError(f"в архиве версия {ver or '?'}, она не новее установленной ({__version__})")
    for need in ("app/main.py", "app/api.py", "app/apply_update.py", "app/selfupdate.py", "Crosspost.bat", "requirements-app.txt"):
        if not (root / need).exists():
            raise ValueError(f"в архиве нет файла {need}")
    bad = []
    for f in root.rglob("*.py"):
        try:
            compile(f.read_bytes(), str(f), "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as e:
            bad.append(f"{f.relative_to(root)}: {e}")
    if bad:
        raise ValueError("в новой версии ошибка в коде: " + bad[0])
    return ver


def prepare(report, release: dict | None = None):
    """Скачать, проверить и подготовить обновление. Ничего в программе не меняет."""
    ok, why = can_update()
    if not ok:
        raise ValueError(why[:1].upper() + why[1:])
    report(0.02, "Запрашиваю выпуск…")
    rel = release or _release()
    ver = str(rel.get("tag_name") or "").lstrip("vV")
    if not updates.newer(ver):
        raise ValueError("У вас уже последняя версия")
    assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
    zname = next((n for n in assets if n and n.lower().endswith(".zip")), None)
    if not zname or f"{zname}.sha256" not in assets:
        raise ValueError("в выпуске нет архива с контрольной суммой (.zip и .zip.sha256) — обновите вручную со страницы выпуска")
    d = _upd()
    shutil.rmtree(d / "dl", ignore_errors=True)
    (d / "dl").mkdir()
    (d / "ready.json").unlink(missing_ok=True)
    zpath, spath = d / "dl" / zname, d / "dl" / (zname + ".sha256")
    _download(assets[f"{zname}.sha256"], spath, report, "Контрольная сумма", 4096)
    m = re.search(r"\b[0-9a-fA-F]{64}\b", spath.read_text(encoding="utf-8", errors="replace"))
    if not m:
        raise ValueError("файл контрольной суммы повреждён")
    _download(assets[zname], zpath, report, "Скачиваю", MAX_ZIP)
    report(0.7, "Проверяю контрольную сумму…")
    if _sha256(zpath).lower() != m.group(0).lower():
        zpath.unlink(missing_ok=True)
        raise ValueError("контрольная сумма не совпала — файл повреждён или подменён, ничего не установлено")
    report(0.8, "Распаковываю…")
    _unpack(zpath, d / "new")
    root = _find_root(d / "new")
    report(0.9, "Проверяю файлы…")
    _check_tree(root, ver)
    (d / "ready.json").write_text(json.dumps({"version": ver, "root": str(root), "at": time.time()}, ensure_ascii=False), encoding="utf-8")
    zpath.unlink(missing_ok=True)
    report(1.0, f"Версия {ver} скачана и проверена")


# ---------------------------------------------------------------- установка

def apply(stop_worker: bool = True, restart: bool = True) -> dict:
    """Остановить фоновую отправку и запустить замену файлов отдельным процессом. Вызывающий закрывает окно."""
    rd = ready()
    if not rd:
        raise ValueError("Обновление не подготовлено — нажмите «Скачать обновление»")
    ok, why = can_update()
    if not ok:
        raise ValueError(why[:1].upper() + why[1:])
    root = Path(rd["root"])
    _check_tree(root, rd["version"])   # файлы с диска могли измениться после проверки
    if stop_worker:
        from app import worker
        if worker.status()["running"]:
            worker.request_stop()
            for _ in range(60):
                time.sleep(0.5)
                if not worker.status()["running"]:
                    break
            else:
                (data_dir() / "worker.stop").unlink(missing_ok=True)
                raise ValueError("Фоновая отправка не остановилась — выключите её в шапке и повторите")
    helper = _upd() / "apply_update.py"
    shutil.copyfile(Path(__file__).with_name("apply_update.py"), helper)
    from app import worker as W
    cmd = [sys.executable, str(helper), "--root", str(app_dir()), "--new", str(root), "--data", str(data_dir()),
           "--pid", str(os.getpid()), "--version", rd["version"], "--python", sys.executable, "--pythonw", W._pythonw()]
    if not restart:
        cmd.append("--no-restart")
    kw = {"cwd": str(_upd()), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        cmd[0] = W._pythonw()
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)
    return {"version": rd["version"]}
