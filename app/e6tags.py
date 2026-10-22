"""База тегов e6ai на этом компьютере: все теги с категорией и числом постов, переименования (алиасы)
и связи «этот тег добавляет другие» (импликации).

Зачем: подсказки теггера JTP обучены на e621 — на e6ai часть тегов называется иначе, часть не используется,
а персонажа сайт сам дополнит франшизой. По этой базе:
  • подсказки теггера переводятся в правильные теги e6ai, несуществующие отбрасываются;
  • при ручном вводе выпадают настоящие теги с категорией и числом постов (поиск понимает старые названия);
  • видно, что сайт добавит сам (импликации), — это не нужно отправлять;
  • у каждого тега — категория для цвета, как на e6ai.

Источник — ежедневная выгрузка сайта (db_export): tags, tag_aliases, tag_implications.
У каждого сайта своя база: e6ai — e6tags-*.db, e621 — e621tags-*.db; действующая — та, куда сейчас загружаем
(«Настройки» → e6ai → «Куда загружать»).
"""
import csv
import gzip
import io
import sqlite3
import threading
import time
from pathlib import Path

from app.paths import data_dir, sub

CATS = {0: "general", 1: "artist", 2: "contributor", 3: "copyright", 4: "character", 5: "species", 6: "invalid", 7: "meta", 8: "lore"}
CAT_ID = {v: k for k, v in CATS.items()}

# разделы формы загрузки e6ai
SEX = ["male", "female", "andromorph", "gynomorph", "herm", "maleherm", "ambiguous_gender"]
COUNT = ["solo", "duo", "trio", "group", "zero_pictured"]
BODY = ["anthro", "feral", "humanoid", "human", "taur"]
CONTENTIOUS = ["gore", "scat", "watersports", "diaper", "my_little_pony", "vore", "not_furry", "rape", "hyper"]

_lock = threading.Lock()


_local = threading.local()     # одно соединение на поток — не открываем базу на каждый тег
_memo = {"mt": None, "path": None, "ready": None, "info": {}, "impl": {}}
_dir = None


def _data() -> Path:
    global _dir
    if _dir is None:
        _dir = data_dir()
    return _dir


_site_memo = {"t": 0.0, "v": "e6ai"}


def site() -> str:
    """Для какого сайта база: куда сейчас загружаем (перечитываем настройку не чаще раза в 3 секунды)."""
    if time.time() - _site_memo["t"] > 3:
        try:
            from app.publish import settings as PS
            _site_memo["v"] = PS.booru_site()
        except Exception:  # noqa: BLE001
            _site_memo["v"] = "e6ai"
        _site_memo["t"] = time.time()
    return _site_memo["v"]


def reset_site():
    _site_memo["t"] = 0.0


def _prefix(s: str | None = None) -> str:
    return "e6tags" if (s or site()) == "e6ai" else f"{s or site()}tags"


def _ptr(s: str | None = None) -> Path:
    """Какая база сейчас действующая. Новая база пишется в новый файл, а указатель переключается:
    в Windows открытый файл нельзя заменить, а окно приложения держит базу открытой."""
    return _data() / f"{_prefix(s)}.current"


def _fresh():
    """Базу пересобрали (указатель сменился) или сменили сайт — сбросить запомненное."""
    try:
        mt = (site(), _ptr().stat().st_mtime)
    except OSError:
        mt = (site(), "legacy")
    if _memo["mt"] != mt:
        path = _data() / f"{_prefix()}.db"
        if mt[1] != "legacy":
            try:
                path = _data() / _ptr().read_text(encoding="utf-8").strip()
            except OSError:
                pass
        _memo.update(mt=mt, path=path, ready=None, info={}, impl={})
    return mt


def _path() -> Path:
    _fresh()
    return _memo["path"]


def _conn() -> sqlite3.Connection:
    mt = _fresh()
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "mt", None) != mt:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        c = sqlite3.connect(str(_path()), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=1")
        c.execute("PRAGMA cache_size=-8000")   # не больше ~8 МБ кэша на соединение
        _local.c, _local.mt = c, mt
    return c


def ready() -> bool:
    _fresh()
    if _memo["ready"] is not None:
        return _memo["ready"]
    ok = False
    if _path().exists():
        try:
            ok = _conn().execute("SELECT 1 FROM tags LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            ok = False
    _memo["ready"] = ok
    return ok


def status() -> dict:
    if not ready():
        return {"ready": False}
    with _conn() as c:
        meta = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM meta")}
        return {"ready": True, "tags": c.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
                "aliases": c.execute("SELECT COUNT(*) FROM aliases").fetchone()[0],
                "implications": c.execute("SELECT COUNT(*) FROM impl").fetchone()[0],
                "updated": float(meta.get("updated", 0)), "day": meta.get("day", "")}


# --- скачивание и сборка ---------------------------------------------------------------------

def _open_csv(path: Path):
    raw = path.read_bytes()
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    return csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace")))


def find_exports(ua: str, s: str | None = None) -> dict:
    """Адреса трёх файлов выгрузки сайта: {"tags": url, "tag_aliases": url, "tag_implications": url, "day": ...}."""
    import re
    import requests
    from app.trends.dumps import SOURCES
    base = SOURCES[s or site()]
    headers = {"User-Agent": ua}
    try:
        r = requests.get(f"{base}/db_exports.json", headers=headers, timeout=30)
        if r.ok:
            items = {x.get("name"): x for x in r.json() if isinstance(x, dict)}
            if {"tags", "tag_aliases", "tag_implications"} <= items.keys():
                out = {k: items[k]["url"] for k in ("tags", "tag_aliases", "tag_implications")}
                out["day"] = str(items["tags"].get("updated_at") or "")[:10]
                return out
    except (ValueError, requests.RequestException):
        pass
    r = requests.get(f"{base}/db_export/", headers=headers, timeout=30)
    r.raise_for_status()
    out = {}
    for kind in ("tags", "tag_aliases", "tag_implications"):
        days = sorted(set(re.findall(rf'\b{kind}-(\d{{4}}-\d{{2}}-\d{{2}})\.csv\.gz\b', r.text)))
        if days:
            out[kind] = f"{base}/db_export/{kind}-{days[-1]}.csv.gz"
            out.setdefault("day", days[-1])
    if set(out) < {"tags", "tag_aliases", "tag_implications"}:
        raise RuntimeError(f"На {s or site()} не нашлось файлов выгрузки тегов — попробуйте позже")
    return out


def _download(url: str, dest: Path, ua: str, report, label: str, lo: float, hi: float) -> Path:
    from app.trends.dumps import download
    dest.unlink(missing_ok=True)
    for attempt in (1, 2):
        download(url, dest, ua, report, label, (lo, hi))
        try:
            with gzip.open(dest) as f:
                while f.read(1 << 20):
                    pass
            return dest
        except (OSError, EOFError):
            dest.unlink(missing_ok=True)
            report(message=f"{label}: файл повреждён, скачиваю заново…")
    raise RuntimeError(f"{label}: файл дважды скачался с повреждениями — попробуйте позже")


def update(report, ua: str, tags_file: Path | None = None, s: str | None = None) -> dict:
    """Скачать выгрузку тегов сайта и собрать базу. tags_file — уже скачанный для трендов файл тегов (не качаем второй раз)."""
    s = s or site()
    folder = sub("dumps", _prefix(s))
    report(progress=0.02, message=f"Ищу выгрузку тегов {s}…")
    urls = find_exports(ua, s)
    files = {}
    if tags_file and Path(tags_file).exists():
        files["tags"] = Path(tags_file)
    else:
        files["tags"] = _download(urls["tags"], folder / "tags.csv.gz", ua, report, "Теги", 0.05, 0.6)
    files["tag_aliases"] = _download(urls["tag_aliases"], folder / "tag_aliases.csv.gz", ua, report, "Переименования", 0.6, 0.75)
    files["tag_implications"] = _download(urls["tag_implications"], folder / "tag_implications.csv.gz", ua, report, "Связи тегов", 0.75, 0.85)
    report(progress=0.88, message="Собираю базу тегов…")
    res = build(files["tags"], files["tag_aliases"], files["tag_implications"], urls.get("day", ""), s)
    report(progress=1.0, message=f"База тегов {s}: {res['tags']:,} тегов, {res['aliases']:,} переименований".replace(",", " "))
    return res


def build(tags_csv: Path, aliases_csv: Path, impl_csv: Path, day: str = "", s: str | None = None) -> dict:
    pre = _prefix(s)
    final = _data() / f"{pre}-{int(time.time())}.db"
    tmp = final.with_name(final.name + ".tmp")
    tmp.unlink(missing_ok=True)
    c = sqlite3.connect(str(tmp))
    c.executescript("""
        CREATE TABLE tags (name TEXT PRIMARY KEY, cat INTEGER, n INTEGER);
        CREATE TABLE aliases (a TEXT PRIMARY KEY, c TEXT);
        CREATE TABLE impl (a TEXT, c TEXT);
        CREATE INDEX impl_a ON impl (a);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    rows = []
    for r in _open_csv(tags_csv):
        try:
            rows.append((r["name"], int(r.get("category") or 0), int(r.get("post_count") or 0)))
        except (ValueError, KeyError):
            continue
    c.executemany("INSERT OR REPLACE INTO tags VALUES (?,?,?)", rows)

    def pairs(path):
        rd = _open_csv(path)
        cols = set(rd.fieldnames or [])
        if not {"antecedent_name", "consequent_name"} <= cols:
            raise RuntimeError(f"{Path(path).name}: неожиданный формат выгрузки ({', '.join(sorted(cols))[:120]})")
        return [(r["antecedent_name"], r["consequent_name"]) for r in rd if active(r)]

    def active(r):
        return (r.get("status") or "active") == "active"
    c.executemany("INSERT OR REPLACE INTO aliases VALUES (?,?)",
                  pairs(aliases_csv))
    c.executemany("INSERT INTO impl VALUES (?,?)",
                  pairs(impl_csv))
    c.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [("updated", str(time.time())), ("day", day)])
    c.commit()
    out = {"tags": len(rows), "aliases": c.execute("SELECT COUNT(*) FROM aliases").fetchone()[0],
           "implications": c.execute("SELECT COUNT(*) FROM impl").fetchone()[0]}
    c.close()
    with _lock:
        tmp.replace(final)
        pt = _ptr(s).with_name(f"{pre}.current.tmp")
        pt.write_text(final.name, encoding="utf-8")
        pt.replace(_ptr(s))
    _memo.update(mt=None)
    for old in list(_data().glob(f"{pre}-*.db")) + [_data() / f"{pre}.db"]:   # старые базы; занятую удалим в следующий раз
        if old != final:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
    return out


# --- запросы ---------------------------------------------------------------------------------

_cache: dict = {}


def canonical(tag: str) -> str:
    tag = str(tag).strip().lower().replace(" ", "_")
    if not ready():
        return tag
    return info([tag])[tag]["name"]


def info(tags) -> dict:
    """{тег: {"name": правильное имя, "cat": "species"…, "n": постов, "exists": bool, "alias_of": старое имя или None}}.
    Без базы — exists=None (неизвестно). Ответы запоминаются, пока база та же."""
    tags = [str(t).strip().lower().replace(" ", "_") for t in tags if str(t).strip()]
    if not ready():
        return {t: {"name": t, "cat": None, "n": None, "exists": None, "alias_of": None} for t in tags}
    memo = _memo["info"]
    if len(memo) > 50000:   # сведения о тегах — не больше 50 тысяч в памяти
        memo.clear()
    miss = [t for t in dict.fromkeys(tags) if t not in memo]
    if miss:
        c = _conn()
        for i in range(0, len(miss), 500):   # пачками: один запрос на сотни тегов
            part = miss[i:i + 500]
            q = ",".join("?" * len(part))
            al = {r["a"]: r["c"] for r in c.execute(f"SELECT a, c FROM aliases WHERE a IN ({q})", part)}
            names = list({al.get(t, t) for t in part})
            q2 = ",".join("?" * len(names))
            rows = {r["name"]: r for r in c.execute(f"SELECT name, cat, n FROM tags WHERE name IN ({q2})", names)}
            for t in part:
                name = al.get(t, t)
                r = rows.get(name)
                memo[t] = {"name": name, "cat": CATS.get(r["cat"], "general") if r else None, "n": r["n"] if r else 0,
                           "exists": bool(r and r["n"] > 0), "alias_of": t if t in al else None}
        if len(memo) > 300000:
            memo.clear()
    return {t: dict(memo[t]) for t in tags if t in memo}


def implied(tags) -> list[str]:
    """Что e6ai добавит сам (импликации, по цепочке): loona_(helluva_boss) → helluva_boss → hellaverse."""
    if not ready():
        return []
    have = {canonical(t) for t in tags}
    seen = set()
    for t in have:
        seen |= _implied_one(t)
    return sorted(seen - have)


def _implied_one(t: str) -> set:
    memo = _memo["impl"]
    if t in memo:
        return memo[t]
    c = _conn()
    out, todo = set(), [t]
    while todo:
        x = todo.pop()
        for r in c.execute("SELECT c FROM impl WHERE a=?", (x,)):
            if r["c"] not in out and r["c"] != t:
                out.add(r["c"])
                todo.append(r["c"])
    memo[t] = out
    return out


def search(q: str, cat: str | None = None, limit: int = 12) -> list[dict]:
    """Подсказки при вводе: сначала теги, начинающиеся с введённого, затем слова внутри; плюс старые названия."""
    q = str(q).strip().lower().replace(" ", "_")
    if not q or not ready():
        return []
    cid = CAT_ID.get(cat) if cat else None
    where = " AND cat=?" if cid is not None else ""
    args_c = (cid,) if cid is not None else ()
    out, seen = [], set()
    with _conn() as c:
        def add(rows, via=None):
            for r in rows:
                if r["name"] in seen or len(out) >= limit:
                    continue
                seen.add(r["name"])
                out.append({"name": r["name"], "cat": CATS.get(r["cat"], "general"), "n": r["n"], "via": via})
        add(c.execute(f"SELECT name, cat, n FROM tags WHERE name >= ? AND name < ? AND n > 0{where} ORDER BY n DESC LIMIT ?",
                      (q, q + "￿", *args_c, limit)))
        if len(out) < limit:   # старое название → правильное
            for a in c.execute("SELECT a, c FROM aliases WHERE a >= ? AND a < ? LIMIT 20", (q, q + "￿")):
                r = c.execute(f"SELECT name, cat, n FROM tags WHERE name=? AND n > 0{where}", (a["c"], *args_c)).fetchone()
                if r:
                    add([r], via=a["a"])
        if len(out) < limit and len(q) >= 3:   # слово внутри: «fox» → red_fox, arctic_fox
            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            add(c.execute(f"SELECT name, cat, n FROM tags WHERE name LIKE ? ESCAPE '\\' AND n > 0{where} ORDER BY n DESC LIMIT ?",
                          (f"%\\_{esc}%", *args_c, limit)))
    out.sort(key=lambda x: (not x["name"].startswith(q) and x["via"] is None, -x["n"]))
    return out[:limit]


def normalize_preds(preds, never=()) -> list:
    """Подсказки теггера → теги e6ai: переименования по алиасам, несуществующие и «invalid» убираем,
    теги из списка «не предлагать» — тоже. Возвращает [[тег, уверенность, категория, постов], …]."""
    never = {str(t).strip().lower().replace(" ", "_") for t in never or ()}
    if not ready() or not preds:
        return [[t, p, None, None] for t, p in (preds or []) if t not in never]
    inf = info([t for t, _ in preds])
    best: dict = {}
    for t, p in preds:
        i = inf.get(str(t).lower().replace(" ", "_"))
        if not i or not i["exists"] or i["cat"] == "invalid":
            continue
        name = i["name"]
        if name in never or t in never:
            continue
        if name not in best or best[name][1] < p:
            best[name] = [name, p, i["cat"], i["n"]]
    return sorted(best.values(), key=lambda x: -x[1])


def drop_implied(tags, keep=()) -> list:
    """Убрать «родителей», которых e6ai добавит сам: при hellhound не нужен canine. keep — не трогать (постоянные)."""
    if not ready():
        return list(tags)
    keep = set(keep)
    tags = list(dict.fromkeys(tags))
    extra = set()
    for t in tags:
        extra |= set(implied([t]))
    return [t for t in tags if t in keep or t not in extra]
