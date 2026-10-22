"""Скачивание ежедневных выгрузок базы e6ai / e621 (db_export)."""
import re
import shutil
from datetime import date
from pathlib import Path

import requests

from app import __version__
from app.paths import sub

SOURCES = {
    "e6ai": "https://e6ai.net",
    "e621": "https://e621.net",
}
_NAME_RE = re.compile(r"\b(posts|tags)-(\d{4}-\d{2}-\d{2})\.csv\.gz\b")


def user_agent(nickname: str) -> str:
    # Правила e621/e6ai: понятный User-Agent с ником, без маскировки под браузер
    return f"Crosspost/{__version__} (by {nickname or 'anonymous'} on e621)"


def dump_dir(source: str) -> Path:
    return sub("dumps", source)


_UNDATED_RE = re.compile(r'href="([^"]*/(posts|tags)\.csv\.gz)"')


def find_latest(source: str, ua: str) -> dict:
    """Ищет свежую выгрузку. Возвращает {"day", "posts": {url, size, sha256}, "tags": {...}}.

    Новый формат (e6ai): служебная страница db_exports.json, файлы posts.csv.gz/tags.csv.gz без даты.
    Старый формат (e621): страница-список с файлами posts-ГГГГ-ММ-ДД.csv.gz.
    """
    base = SOURCES[source]
    headers = {"User-Agent": ua}
    # 1) служебная страница с описанием выгрузок
    try:
        r = requests.get(f"{base}/db_exports.json", headers=headers, timeout=30)
        if r.ok:
            items = {x.get("name"): x for x in r.json() if isinstance(x, dict)}
            if "posts" in items and "tags" in items:
                p, t = items["posts"], items["tags"]
                day = str(p.get("updated_at") or "")[:10] or date.today().isoformat()
                return {"day": day,
                        "posts": {"url": p["url"], "size": p.get("file_size"), "sha256": p.get("checksum")},
                        "tags": {"url": t["url"], "size": t.get("file_size"), "sha256": t.get("checksum")}}
    except (ValueError, requests.RequestException):
        pass
    # 2) страница-список
    r = requests.get(f"{base}/db_export/", headers=headers, timeout=30)
    r.raise_for_status()
    found: dict[str, set] = {}
    for kind, day in _NAME_RE.findall(r.text):
        found.setdefault(day, set()).add(kind)
    days = sorted(d for d, kinds in found.items() if {"posts", "tags"} <= kinds)
    if days:
        d = days[-1]
        return {"day": d, "posts": {"url": f"{base}/db_export/posts-{d}.csv.gz"},
                "tags": {"url": f"{base}/db_export/tags-{d}.csv.gz"}}
    undated = {kind: url for url, kind in _UNDATED_RE.findall(r.text)}
    if {"posts", "tags"} <= undated.keys():
        absu = lambda u: u if u.startswith("http") else base + u  # noqa: E731
        return {"day": date.today().isoformat(), "posts": {"url": absu(undated["posts"])},
                "tags": {"url": absu(undated["tags"])}}
    raise RuntimeError(f"{source}: на странице выгрузок не нашлось файлов posts/tags — "
                       f"скачайте их вручную с {base}/db_export/ и подключите кнопкой ниже")


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _gzip_ok(path: Path) -> bool:
    """Файл — целый gzip (дочитывается до конца без ошибок)."""
    import gzip
    import zlib
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1 << 22):
                pass
        return True
    except (OSError, EOFError, zlib.error):
        return False


def download(url: str, dest: Path, ua: str, report, label: str, share: tuple[float, float], version: str = ""):
    """Скачивает с докачкой (.part). share — какую часть общего прогресса занимает этот файл.

    version — отпечаток файла на сервере (контрольная сумма). Недокачанный кусок от другой версии
    не продолжаем: сайт пересобирает выгрузку каждый день под тем же адресом, и склейка двух версий
    давала «повреждённый» файл.
    """
    if dest.exists():
        return dest
    part = dest.with_name(dest.name + ".part")
    stamp = dest.with_name(dest.name + ".part.ver")
    if part.exists() and (stamp.read_text(encoding="utf-8") if stamp.exists() else "") != version:
        part.unlink(missing_ok=True)
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": ua, "Accept-Encoding": "identity"}   # файл уже .gz — без распаковки «на лету»
    if have:
        headers["Range"] = f"bytes={have}-"
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 416:  # уже всё скачано
            part.rename(dest)
            stamp.unlink(missing_ok=True)
            return dest
        r.raise_for_status()
        if have and r.status_code != 206:  # сервер не умеет докачку — начинаем заново
            have = 0
        total = int(r.headers.get("Content-Length") or 0) + have
        mode = "ab" if have else "wb"
        done = have
        stamp.write_text(version, encoding="utf-8")
        with open(part, mode) as f:
            for chunk in r.raw.stream(1 << 20, decode_content=False):   # байты как есть
                f.write(chunk)
                done += len(chunk)
                frac = done / total if total else 0
                report(progress=share[0] + (share[1] - share[0]) * frac,
                       message=f"{label}: {done / 1e6:,.0f} из {total / 1e6:,.0f} МБ".replace(",", " "))
    part.replace(dest)
    stamp.unlink(missing_ok=True)
    return dest


def local_latest(source: str) -> tuple[str, Path, Path] | None:
    d = dump_dir(source)
    days = sorted({m.group(2) for p in d.glob("*.csv.gz") if (m := _NAME_RE.search(p.name))})
    for day in reversed(days):
        posts, tags = d / f"posts-{day}.csv.gz", d / f"tags-{day}.csv.gz"
        if posts.exists() and tags.exists():
            return day, posts, tags
    return None


def ensure_latest(source: str, ua: str, report) -> tuple[str, Path, Path]:
    report(progress=0.01, message="Ищу свежую выгрузку…")
    info = find_latest(source, ua)
    day = info["day"]
    d = dump_dir(source)
    out = {}
    for kind, label, share in (("tags", "Теги", (0.01, 0.05)), ("posts", "Посты", (0.05, 0.6))):
        want = (info[kind].get("sha256") or "").lower()
        url = info[kind]["url"]
        if want:   # адрес с отпечатком — чтобы кэш сайта не отдал вчерашний файл
            url += ("&" if "?" in url else "?") + "v=" + want[:16]
        dest = d / f"{kind}-{day}.csv.gz"
        for attempt in (1, 2):
            try:
                path = download(url, dest, ua, report, label, share, want)
            except requests.HTTPError:
                if url == info[kind]["url"]:
                    raise
                url = info[kind]["url"]   # сайт не принял адрес с отпечатком — берём обычный
                path = download(url, dest, ua, report, label, share, want)
            if not want:
                ok = _gzip_ok(path)
                if ok:
                    break
            else:
                report(message=f"{label}: проверяю целостность…")
                if _sha256(path) == want:
                    break
                # сумма не сошлась: если сам архив целый — это просто соседняя версия выгрузки, она годится
                if _gzip_ok(path):
                    report(message=f"{label}: версия файла чуть отличается от объявленной — архив целый, беру его")
                    break
            path.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError(f"Файл {kind} дважды скачался с повреждениями. Скорее всего, рвётся связь "
                                   f"(VPN/антивирус?). Попробуйте позже или скачайте файлы вручную и подключите ниже.")
            report(message=f"{label}: файл повреждён, скачиваю заново…")
        out[kind] = path
    for p in d.glob("*.csv.gz*"):  # старые выгрузки больше не нужны
        if day not in p.name:
            p.unlink(missing_ok=True)
    return day, out["posts"], out["tags"]


def import_files(source: str, posts_src: str, tags_src: str) -> tuple[str, Path, Path]:
    """Подключает вручную скачанные файлы выгрузки."""
    ps, ts = Path(posts_src), Path(tags_src)
    for p in (ps, ts):
        if not p.exists():
            raise FileNotFoundError(f"Файл не найден: {p}")
    m = _NAME_RE.search(ps.name)
    day = m.group(2) if m else date.today().isoformat()
    d = dump_dir(source)
    posts, tags = d / f"posts-{day}.csv.gz", d / f"tags-{day}.csv.gz"
    if ps.resolve() != posts.resolve():
        shutil.copyfile(ps, posts)
    if ts.resolve() != tags.resolve():
        shutil.copyfile(ts, tags)
    return day, posts, tags
