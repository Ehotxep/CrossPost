"""Сборка выпуска: dist/Crosspost-<версия>.zip и dist/Crosspost-<версия>.zip.sha256.

  python tools/make_release.py

Оба файла прикладываются к выпуску (Release) на GitHub с тегом v<версия>. Программа качает именно их:
контрольная сумма нужна, чтобы подмену или повреждение файла было видно до установки.
В архив не попадают секреты, личные данные и служебные папки.
"""
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", ".github", "dist", "Файлы", "Files", "data", "lpub", "venv", ".venv", "tests", ".update-backup"}
SKIP_FILES = {"config.toml", "tokens.json", ".write-test"}
SKIP_SUFFIX = {".pyc", ".zip", ".log", ".session", ".db"}


def version() -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', (ROOT / "app" / "__init__.py").read_text(encoding="utf-8"))
    if not m:
        sys.exit("Не нашёл __version__ в app/__init__.py")
    return m.group(1)


def main() -> int:
    ver = version()
    if f"## {ver}" not in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"):
        print(f"Предупреждение: в CHANGELOG.md нет раздела «## {ver}».")
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    name = f"Crosspost-{ver}.zip"
    zp = out / name
    n = 0
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(ROOT.rglob("*")):
            rel = f.relative_to(ROOT)
            if not f.is_file() or set(rel.parts) & SKIP_DIRS or f.name in SKIP_FILES or f.suffix in SKIP_SUFFIX:
                continue
            z.write(f, f"Crosspost/{rel.as_posix()}")
            n += 1
    h = hashlib.sha256(zp.read_bytes()).hexdigest()
    (out / f"{name}.sha256").write_text(f"{h}  {name}\n", encoding="utf-8")
    print(f"Готово: {zp} ({n} файлов, {zp.stat().st_size // 1024} КБ)\nSHA-256: {h}")
    print(f"К выпуску v{ver} на GitHub приложите оба файла: {name} и {name}.sha256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
