"""Проверка тегов перед загрузкой на e6ai — как форма загрузки самого сайта, только короче.

Обязательное (без этого e6ai не загружаем): директор, пол, сколько персонажей, тип тела, вид,
решение по персонажу (тег или «без тега персонажа»), решение по спорному (тег или «ничего из этого»).
Ошибки (тоже блокируют): противоречия (глаза закрыты и один глаз закрыт…), несколько вариантов «сколько»,
теги-«invalid», рейтинг SFW при откровенных тегах.
Предупреждения (нужно подтвердить «Всё верно»): несуществующие на e6ai теги, мало тегов, solo с двумя полами…

Здесь же — начальный выбор тегов из подсказок теггера и строки компактной формы.
"""
import hashlib
import re

from app import e6tags as DB

SEX, COUNT, BODY, CONTENTIOUS = DB.SEX, DB.COUNT, DB.BODY, DB.CONTENTIOUS
SEX_RU = {"male": "Male", "female": "Female", "andromorph": "Andromorph", "gynomorph": "Gynomorph", "herm": "Herm",
          "maleherm": "Male-Herm", "ambiguous_gender": "Ambiguous"}
MIN_GOOD = 15

# мелкие детали теггер часто путает (так модераторы и сняли ring_piercing) — сами не отмечаем никогда
DETAIL_RE = re.compile(r"piercing|jewelry|jewellery|(^|_)rings?($|_)|earring|necklace|bracelet|anklet|tattoo|accessor|"
                       r"(^|_)stud|choker|(^|_)collar|bangle|pendant|brooch|wristband|armband|bandanna|hairpin|hair_clip")

# взаимоисключающие теги
CONFLICTS = [
    ({"eyes_closed", "one_eye_closed"}, "глаза закрыты и один глаз закрыт одновременно"),
    ({"eyes_closed", "open_eyes"}, "глаза и закрыты, и открыты"),
    ({"clothed", "nude"}, "одет и раздет одновременно"),
    ({"fully_clothed", "nude"}, "одет и раздет одновременно"),
    ({"mouth_closed", "open_mouth"}, "рот и закрыт, и открыт"),
    ({"looking_at_viewer", "looking_away"}, "смотрит на зрителя и в сторону одновременно"),
    ({"smile", "frown"}, "улыбается и хмурится одновременно"),
]
EXPLICIT = {"sex", "penis", "pussy", "genitals", "anus", "erection", "vaginal_penetration", "anal_penetration", "penetration",
            "cum", "balls", "masturbation", "oral", "fellatio", "cunnilingus", "nipples", "breasts", "nude", "butt"}
EXPLICIT_HARD = {"sex", "penis", "pussy", "genitals", "anus", "erection", "penetration", "vaginal_penetration",
                 "anal_penetration", "cum", "balls", "masturbation", "fellatio", "cunnilingus"}


def _norm(t) -> str:
    return str(t).strip().lower().replace(" ", "_")


def _cat_fallback(t: str) -> str:
    from app.publish import hashtags as HT
    c = HT.category(t)
    return {"franchise": "copyright"}.get(c, c or "general")


def cats(tags) -> dict:
    """{тег: категория e6ai}. С базой — точно; без неё — по догадке (виды из трендов, «имя_(франшиза)» → персонаж)."""
    tags = [_norm(t) for t in tags if str(t).strip()]
    if DB.ready():
        inf = DB.info(tags)
        return {t: (inf[t]["cat"] or "invalid") for t in tags}
    return {t: _cat_fallback(t) for t in tags}


def signature(tags, rating, meta) -> str:
    meta = meta or {}
    raw = "|".join(sorted({_norm(t) for t in tags})) + f"#{rating}#{int(bool(meta.get('nochar')))}{int(bool(meta.get('nocont')))}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


RARE = 5          # тег, у которого на e6ai меньше постов, — скорее ошибка теггера: сам не отмечается
LEARN_LOW = 0.25  # тег, который модераторы добавляли не меньше двух раз, отмечается уже от этой уверенности


def _learn() -> dict:
    try:
        from app.insights import e6learn
        return e6learn.learned()
    except Exception:
        return {}


def initial(preds, preselect: float, learn: dict | None = None) -> list[str]:
    """Что отметить сразу из подсказок теггера: только уверенное, без мелких деталей, без редких тегов,
    без убранного модераторами, «сколько» — один вариант, без «родителей», которых e6ai добавит сам."""
    th = max(float(preselect or 0), 0.5)
    learn = _learn() if learn is None else learn

    def ok(t, p, n):
        L = learn.get(t) or {}
        if L.get("removed", 0) > L.get("added", 0):
            return False
        if n is not None and n < RARE:
            return False
        if DETAIL_RE.search(t) and L.get("added", 0) < 2:
            return False
        return p >= th or (L.get("added", 0) >= 2 and p >= LEARN_LOW)
    pick = [x[0] for x in preds if ok(x[0], x[1], x[3] if len(x) > 3 else None)]
    cnt = [t for t in pick if t in COUNT]
    if len(cnt) > 1:
        best = max(cnt, key=lambda t: next(p for x, p, *_ in preds if x == t))
        pick = [t for t in pick if t not in COUNT or t == best]
    if "eyes_closed" in pick and "one_eye_closed" in pick:   # спорная пара — берём более уверенную
        pr = {t: p for t, p, *_ in preds}
        pick.remove("one_eye_closed" if pr["eyes_closed"] >= pr["one_eye_closed"] else "eyes_closed")
    return DB.drop_implied(pick)


def check(tags, consts, meta, rating) -> dict:
    """tags — выбранные в посте, consts — постоянные из настроек. Возвращает
    {"need": [что не заполнено], "errors": [...], "warnings": [...], "implied": [...], "total": n,
     "renamed": {старое: новое}, "sig": отпечаток для «Всё верно», "ready": bool, "db": bool}."""
    meta = meta or {}
    db = DB.ready()
    sel = [_norm(t) for t in list(consts or []) + list(tags or []) if str(t).strip()]
    renamed = {}
    if db:
        inf = DB.info(sel)
        renamed = {t: i["name"] for t, i in inf.items() if i["alias_of"]}
        sel = list(dict.fromkeys(renamed.get(t, t) for t in sel))
    implied = DB.implied(sel) if db else []
    full = set(sel) | set(implied)
    cat = cats(list(full))
    in_cat = lambda c: [t for t in full if cat.get(t) == c]
    zero = "zero_pictured" in full

    need = []
    const_set = {renamed.get(_norm(t), _norm(t)) for t in consts or []}
    exists = DB.info(sel) if db else {}
    new_const = [t for t in const_set if db and not exists[t]["exists"]]   # свой новый тег директора ещё не в выгрузке
    if not (in_cat("artist") or new_const or (not db and const_set)):
        need.append("директор" if DB.site() == "e6ai" else "художник")
    if not zero:
        if not full & set(SEX):
            need.append("пол")
        if not full & set(COUNT):
            need.append("сколько")
        if not full & set(BODY):
            need.append("тип тела")
        if not in_cat("species"):
            need.append("вид")
        if not (in_cat("character") or meta.get("nochar")):
            need.append("персонаж")
    elif not full & set(COUNT):
        need.append("сколько")
    if not (full & set(CONTENTIOUS) or meta.get("nocont")):
        need.append("спорное")

    errors, warnings = [], []
    for pair, why in CONFLICTS:
        if pair <= full:
            errors.append({"text": why, "tags": sorted(pair)})
    cnt = sorted(full & set(COUNT))
    if len(cnt) > 1:
        errors.append({"text": "несколько вариантов «сколько»", "tags": cnt})
    bad = [t for t in sel if cat.get(t) == "invalid"]
    if db:
        unknown = [t for t in sel if not exists[t]["exists"] and t not in const_set]
        bad = [t for t in bad if exists[t]["exists"]]
        if unknown:
            warnings.append({"text": f"таких тегов на {DB.site()} нет — сайт создаст новые, модераторы их уберут", "tags": unknown})
    if bad:
        errors.append({"text": f"тег помечен на {DB.site()} как недопустимый", "tags": bad})
    hard = sorted(full & EXPLICIT_HARD)
    if rating == "sfw" and (hard or "nude" in full):
        errors.append({"text": "рейтинг SFW, а в тегах откровенное", "tags": hard or ["nude"]})
    elif rating == "erotic" and hard:
        warnings.append({"text": "рейтинг «Эротика», а теги похожи на откровенное (e)", "tags": hard})
    if "solo" in full:
        sx = sorted(full & set(SEX))
        if len(sx) > 1:
            warnings.append({"text": "solo, но отмечено несколько полов", "tags": sx})
        bd = sorted(full & set(BODY))
        if len(bd) > 1:
            warnings.append({"text": "solo, но отмечено несколько типов тела", "tags": bd})
    if zero and full & set(SEX):
        warnings.append({"text": "zero pictured, но отмечен пол", "tags": sorted(full & set(SEX))})
    if len(full) < MIN_GOOD:
        warnings.append({"text": f"всего {len(full)} {_pl(len(full))} — модераторы ждут хотя бы {MIN_GOOD}: фон, поза, одежда, выражение", "tags": []})

    sig = signature(sel, rating, meta)
    return {"need": need, "errors": errors, "warnings": warnings, "implied": sorted(implied), "total": len(full),
            "renamed": renamed, "sig": sig, "confirmed": meta.get("ok") == sig, "db": db,
            "ready": not need and not errors and meta.get("ok") == sig}


def _pl(n):
    return "тег" if n % 10 == 1 and n % 100 != 11 else "тега" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "тегов"


def blockers(c: dict) -> list[str]:
    """Короткий список «нужно: …» для «Куда уйдёт»."""
    out = list(c["need"])
    if c["errors"]:
        out.append("исправить противоречия")
    if not out and not c["confirmed"]:
        out.append("подтвердить теги")
    return out


def form(preds, tags, consts, meta, rating, show_min=0.2, preselect=0.35) -> dict:
    """Всё для компактной формы одной картинки: строки (выбрано / подсказки теггера), проверка."""
    preds = [p for p in (preds or []) if p[1] >= show_min]
    prob = {t: p for t, p, *_ in preds}
    posts = {x[0]: x[3] for x in preds if len(x) > 3}
    learn = _learn()
    sel = [_norm(t) for t in tags or []]
    c0 = check(sel, consts, meta, rating)
    sel = list(dict.fromkeys(c0["renamed"].get(t, t) for t in sel))
    trimmed = [t for t in sel if t not in DB.drop_implied(sel)]   # лишние «родители» — e6ai добавит их сам
    if trimmed:
        sel = [t for t in sel if t not in trimmed]
    c = check(sel, consts, meta, rating)
    c["renamed"] = c0["renamed"]
    c["trimmed"] = trimmed
    consts_n = [c["renamed"].get(_norm(t), _norm(t)) for t in consts or []]
    every = list(dict.fromkeys(consts_n + sel + list(prob) + c["implied"]))
    cat = cats(every)
    selset, cset, impl = set(sel), set(consts_n), set(c["implied"])
    th = max(float(preselect or 0), 0.5)

    def opt(t):
        L = learn.get(t) or {}
        n = posts.get(t)
        return {"t": t, "on": t in selset or t in cset, "p": prob.get(t), "lock": t in cset, "imp": t in impl and t not in selset,
                "sure": (prob.get(t) or 0) >= th and not DETAIL_RE.search(t),
                "rare": n if n is not None and n < RARE else None, "mrem": L.get("removed", 0), "madd": L.get("added", 0)}

    def fixed(opts):
        return [opt(t) for t in opts]

    def by_cat(cs, exclude=()):
        lst = [t for t in every if cat.get(t) in cs and t not in exclude]
        lst.sort(key=lambda t: (not (t in selset or t in cset), t not in impl, -(prob.get(t) or 0)))
        return [opt(t) for t in lst]

    special = set(SEX) | set(COUNT) | set(BODY) | set(CONTENTIOUS)
    rows = {
        # без базы категорий не знаем — показываем все постоянные теги (в них и директор)
        "director": [opt(t) for t in every if cat.get(t) in ("artist", "contributor")] if c["db"] else [opt(t) for t in consts_n],
        "character": by_cat({"character", "copyright"}),
        "sex": fixed(SEX), "count": fixed(COUNT), "body": fixed(BODY),
        "species": by_cat({"species"}),
        "cont": fixed(CONTENTIOUS),
        "rest": by_cat({"general", "meta", "lore", "invalid"}, exclude=special | (set() if c["db"] else cset)),
    }
    return {"tags": sel, "rows": rows, "check": c, "blockers": blockers(c), "cats": {t: cat.get(t) for t in every}}
