"""Значок Crosspost: файл .ico для окна и панели задач Windows, картинка для значка в трее.

Основной значок — готовый файл app/web/crosspost.ico из поставки. Если его нет, рисуется кодом:
скруглённый квадрат с переходом цвета и белый «самолётик» (и записывается в тот же файл).
"""
from pathlib import Path

ICO = Path(__file__).resolve().parent / "web" / "crosspost.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
C1, C2 = (91, 103, 240), (176, 108, 255)   # как .logo в style.css: #5B67F0 → #B06CFF


def active_ico() -> Path:
    """Значок приложения — файл из поставки (старый «свой значок» из папки данных больше не используется)."""
    try:
        from app.paths import data_dir
        (data_dir() / "icon.ico").unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return ensure_ico()


def image(size: int = 256):
    """Значок нужного размера: из файла поставки; если его нет — рисуем (вчетверо крупнее и уменьшаем — края гладкие)."""
    from PIL import Image, ImageDraw
    try:   # готовый значок из поставки (app/web/crosspost.ico) — главнее нарисованного
        if ICO.exists():
            with Image.open(ICO) as im:
                big = im.ico.getimage(max(im.ico.sizes())) if hasattr(im, "ico") else im
                return big.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        pass
    S = size * 4
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)   # по диагонали, как linear-gradient(135deg)
            px[x, y] = tuple(round(C1[i] + (C2[i] - C1[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (S, S), 0)
    pad = round(S * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, S - pad, S - pad), radius=round(S * 0.24), fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(out)
    k = S / 64   # «самолётик» в координатах 64×64
    d.polygon([(19 * k, 17 * k), (49 * k, 32 * k), (19 * k, 47 * k), (25.5 * k, 32 * k)], fill=(255, 255, 255, 255))
    d.polygon([(25.5 * k, 32 * k), (49 * k, 32 * k), (19 * k, 47 * k)], fill=(232, 228, 255, 255))   # нижняя грань чуть темнее
    return out.resize((size, size), Image.LANCZOS)


def ensure_ico() -> Path:
    """Файл .ico со всеми размерами для Windows (создаётся, если его нет)."""
    if not ICO.exists():
        img = image(256)
        img.save(ICO, format="ICO", sizes=[(s, s) for s in SIZES])
    return ICO


def set_app_id():
    """Windows: своё «имя» приложения — иначе панель задач считает окно частью python.exe и рисует значок Python."""
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Crosspost.App")
    except Exception:  # noqa: BLE001 — не критично
        pass


def set_window_icon(title: str, hwnd: int | None = None, timeout: float = 15.0):
    """Windows: поставить окну значок Crosspost (большой — для панели задач и Alt+Tab, маленький — для заголовка).
    Окно ищется по заголовку, если его номер не известен."""
    import sys
    import time
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    ico = str(active_ico())
    deadline = time.time() + timeout
    while not hwnd and time.time() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            time.sleep(0.2)
    if not hwnd:
        return False
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
    big = user32.LoadImageW(None, ico, IMAGE_ICON, user32.GetSystemMetrics(11), user32.GetSystemMetrics(12), LR_LOADFROMFILE)
    small = user32.LoadImageW(None, ico, IMAGE_ICON, user32.GetSystemMetrics(49), user32.GetSystemMetrics(50), LR_LOADFROMFILE)
    if big:
        user32.SendMessageW(hwnd, WM_SETICON, 1, big)
    if small:
        user32.SendMessageW(hwnd, WM_SETICON, 0, small)
    return bool(big or small)


def focus_window(title: str) -> bool:
    """Windows: развернуть окно и вывести на передний план (по заголовку)."""
    import sys
    if sys.platform != "win32" or not title:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        # Windows не даёт чужому процессу забирать фокус — краткий «Alt» снимает это ограничение
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


def create_shortcuts() -> dict:
    """Windows: ярлыки «Crosspost» на рабочем столе и в меню «Пуск» — со значком приложения и запуском без .bat
    (поэтому без окна «Не удаётся проверить издателя»). Создаются через WScript, как ярлык автозапуска."""
    import os
    import subprocess
    import sys
    import tempfile
    if sys.platform != "win32":
        raise RuntimeError("Ярлыки создаются только в Windows")
    from app.worker import ROOT, _pythonw
    ico = str(active_ico())
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    start = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Crosspost.lnk"
    lines = ['Set sh = CreateObject("WScript.Shell")',
             'desk = sh.SpecialFolders("Desktop")']
    for target in ('desk & "\\Crosspost.lnk"', f'"{start}"'):
        lines += [f"Set s = sh.CreateShortcut({target})",
                  f's.TargetPath = "{_pythonw()}"', 's.Arguments = "-m app"', f's.WorkingDirectory = "{ROOT}"',
                  f's.IconLocation = "{ico},0"', 's.Description = "Crosspost — кросспостинг артов"', "s.Save"]
    lines.append('WScript.Echo desk')
    fd, tmp = tempfile.mkstemp(suffix=".vbs")
    os.close(fd)
    try:
        Path(tmp).write_text("\r\n".join(lines) + "\r\n", encoding="utf-16")   # кириллица в путях не ломается
        r = subprocess.run(["cscript", "//nologo", tmp], capture_output=True, text=True, timeout=30,
                           creationflags=0x08000000)
        if r.returncode != 0:
            raise RuntimeError("Не получилось создать ярлык: " + (r.stderr or r.stdout).strip()[:200])
        desk = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    finally:
        Path(tmp).unlink(missing_ok=True)
    return {"desktop": str(Path(desk) / "Crosspost.lnk") if desk else "", "start": str(start)}
