"""Хэштеги для постов в Telegram, Discord и ВК (на e6ai не добавляются — там свои теги).

Порядок, как вы уже пишете в канале: рейтинг (#sfw / #nsfw), персонажи и франшизы, затем вид (#cat, #fox).
Откуда берём:
  • персонажи/франшизы — из тегов e6ai, которые вы отметили или дописали в посте;
  • вид — из тех же тегов и из уверенных подсказок теггера.
Категорию тега (вид, персонаж, франшиза) знаем из выгрузки e621/e6ai для трендов; если её нет —
по встроенному списку частых видов.
"""
import json
import re

from app.paths import data_dir

# Слишком общие «виды» e621 — в хэштеги не берём (вместо «mammal» нужен «fox»)
BROAD = {
    "mammal", "canid", "canine", "canis", "vulpine", "felid", "feline", "pantherine", "bird", "avian", "reptile",
    "scalie", "lagomorph", "leporid", "rodent", "murid", "murine", "mustelid", "ursid", "equid", "equine", "bovid",
    "bovine", "caprine", "cervid", "procyonid", "hyaenid", "marsupial", "primate", "haplorhine", "fish", "marine",
    "arthropod", "insect", "humanoid", "animal_humanoid", "mammal_humanoid", "felid_humanoid", "canid_humanoid",
    "human", "hybrid", "anthro", "feral", "taur", "lizard", "squamate", "galliform", "passerine", "accipitrid",
    "musteline", "sciurid", "viverrid", "herpestid", "suid", "camelid", "giraffid", "hippopotamid", "pinniped",
    "cetacean", "toothed_whale", "elephantid", "proboscidean", "chiropteran", "monotreme", "amphibian", "anuran",
    "theropod", "dinosaur", "prehistoric_species", "mythological_creature", "mythological_canine", "fictional_species",
}
ALIAS = {"domestic_cat": "cat", "domestic_dog": "dog", "red_fox": "fox", "arctic_fox": "fox", "fennec_fox": "fennec",
         "gray_wolf": "wolf", "domestic_rabbit": "rabbit", "domestic_ferret": "ferret", "house_mouse": "mouse",
         "domestic_pig": "pig", "domestic_goat": "goat", "domestic_sheep": "sheep"}
FALLBACK_SPECIES = {
    "fox", "red_fox", "arctic_fox", "fennec_fox", "fennec", "wolf", "gray_wolf", "domestic_cat", "cat", "domestic_dog",
    "dog", "lion", "tiger", "leopard", "snow_leopard", "cheetah", "lynx", "cougar", "jaguar", "hyena", "rabbit",
    "domestic_rabbit", "hare", "bat", "dragon", "western_dragon", "eastern_dragon", "wyvern", "deer", "horse", "pony",
    "unicorn", "pegasus", "bear", "panda", "red_panda", "raccoon", "otter", "ferret", "skunk", "squirrel", "mouse",
    "rat", "kangaroo", "kobold", "sergal", "protogen", "synth_(vader-san)", "avali", "shark", "orca", "dolphin",
    "goat", "sheep", "cow", "pig", "boar", "crocodile", "snake", "eagle", "owl", "crow", "hawk", "gryphon", "phoenix",
    "werewolf", "jackal", "coyote", "husky", "german_shepherd", "dobermann", "corgi", "border_collie", "fennec",
    "dingo", "maned_wolf", "civet", "mongoose", "meerkat", "badger", "wolverine", "weasel", "mink", "hamster",
    "chinchilla", "hedgehog", "lizard", "gecko", "frog", "goo_creature", "slime", "demon", "angel", "imp", "succubus",
    "hellhound", "kitsune", "nine-tailed_fox", "tanuki", "possum", "opossum", "koala", "zebra", "giraffe", "elephant",
    "rhinoceros", "hippopotamus", "camel", "llama", "alpaca", "moth", "bee", "spider", "ant", "dragonfly",
}

_cache = {"mtime": None, "cats": {}}


def categories() -> dict:
    """{тег: категория} из выгрузок e621 и e6ai (раздел «Тренды»)."""
    files = [data_dir() / "trends" / s / "meta.json" for s in ("e621", "e6ai")]
    mt = tuple(f.stat().st_mtime if f.exists() else 0 for f in files)
    if _cache["mtime"] != mt:
        cats = {}
        for f in files:
            if f.exists():
                try:
                    cats.update(json.loads(f.read_text(encoding="utf-8")).get("categories") or {})
                except Exception:
                    pass
        _cache.update(mtime=mt, cats=cats)
    return _cache["cats"]


def category(tag: str) -> str | None:
    from app import e6tags
    if e6tags.ready():   # точная категория из базы тегов e6ai
        i = e6tags.info([tag])[tag.strip().lower().replace(" ", "_")]
        if i["exists"]:
            return {"copyright": "franchise", "general": None, "invalid": None, "artist": "artist", "contributor": "artist"}.get(i["cat"], i["cat"])
    c = categories().get(tag)
    if c:
        return c
    if tag in FALLBACK_SPECIES:
        return "species"
    if tag.endswith(")") and "_(" in tag and not tag.endswith("_(artwork)"):   # имя_(франшиза) — почти всегда персонаж
        return "character"
    return None


def to_hashtag(tag: str) -> str:
    """loona_(helluva_boss) → loona; snow-leopard → snow_leopard (в хэштегах нельзя дефис и скобки)."""
    tag = ALIAS.get(tag, tag)
    tag = re.sub(r"_\([^)]*\)$", "", tag.strip().lower())
    tag = re.sub(r"[^\w]+", "_", tag, flags=re.UNICODE).strip("_")
    return tag


def _preds(file_id: str) -> list:
    from app.paths import sub
    f = sub("uploads") / f"{file_id}.tags.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except Exception:
        return []


def suggest(cfg: dict, draft: dict) -> list[str]:
    """Подсказка: рейтинг → персонажи → франшизы → виды → постоянные из настроек."""
    hc = cfg["hashtags"]
    out = []

    def add(tag):
        h = to_hashtag(tag)
        if h and h not in out and len(out) < 15:
            out.append(h)

    r = (hc.get("rating_tags") or {}).get(draft.get("rating") or "")
    if r:
        add(r)
    chosen = []
    for tags in (draft.get("e6ai_tags") or {}).values():
        chosen += list(tags or [])
    for cat in ("character", "franchise"):   # персонажей теггер не знает — берём то, что вы отметили для e6ai
        for t in chosen:
            if category(t) == cat:
                add(t)
    species, seen = [], set()
    for t in chosen:
        if category(t) == "species" and t not in BROAD and t not in seen:
            seen.add(t)
            species.append((t, 1.0))
    min_p = float(hc.get("min_prob", 0.5))
    for fid in draft.get("files") or []:
        for t, p in _preds(fid):
            if p >= min_p and category(t) == "species" and t not in BROAD and t not in seen:
                seen.add(t)
                species.append((t, p))
    species.sort(key=lambda x: -x[1])
    for t, _ in species[: int(hc.get("max_species", 3))]:
        add(t)
    for t in hc.get("constant") or []:
        add(t)
    return out


def final(cfg: dict, draft: dict) -> list[str]:
    """Что уйдёт в пост: выбранные вручную, а если их не трогали — подсказка."""
    if draft.get("hashtags") is not None:
        return [h for h in (to_hashtag(t) for t in draft["hashtags"]) if h]
    return suggest(cfg, draft)


def apply(cfg: dict, draft: dict, text: str, platform: str) -> str:
    """Добавить строку хэштегов к тексту (без повторов тех, что уже написаны в тексте)."""
    hc = cfg["hashtags"]
    if not hc.get("enabled", True) or not (hc.get("use") or {}).get(platform, True):
        return text
    have = {t.lower() for t in re.findall(r"#(\w+)", text or "", flags=re.UNICODE)}
    tags = [t for t in final(cfg, draft) if t.lower() not in have]
    if not tags:
        return text
    ln = " ".join("#" + t for t in tags)
    if not text:
        return ln
    return f"{text}\n\n{ln}" if hc.get("position") == "end" else f"{ln}\n{text}"
