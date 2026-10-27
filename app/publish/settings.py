"""Настройки публикации: куда и как постить. Хранятся в папке данных (publish.json), только на этом компьютере."""
import copy
import json

from app.paths import data_dir

RATINGS = ("sfw", "erotic", "explicit")          # рейтинг поста
DEST_RATINGS = ("sfw", "nsfw", "all")            # рейтинг места: только SFW / только 18+ / любой
ORIGINALS = ("reply", "comments", "none")

PLATFORM_MODULES = ("telegram", "discord", "vk", "bluesky", "deviantart", "e6ai")


def platform_order(cfg: dict | None = None) -> list:
    """Порядок площадок, выбранный пользователем: без неизвестных и повторов, недостающие — в конец."""
    raw = (cfg or {}).get("platform_order") if cfg is not None else None
    out = []
    for k in (raw if isinstance(raw, list) else []):
        if k in PLATFORM_MODULES and k not in out:
            out.append(k)
    return out + [k for k in PLATFORM_MODULES if k not in out]
SCREEN_MODULES = ("history", "stats", "trends")
# дополнительные функции — всё, кроме самого кросспостинга
EXTRA_MODULES = ("tagger", "collect", "news", "forecast", "crosspost", "targeting", "translation")   # crosspost — экраны «Новый пост» и «История»

DEFAULTS = {
    "platform_order": list(PLATFORM_MODULES),   # в каком порядке показывать площадки: настройки, подписи, окно поста
    # отключаемые модули: площадки (не участвуют в публикации, не показываются) и экраны
    "modules": {"telegram": True, "discord": True, "vk": True, "bluesky": True, "deviantart": True, "e6ai": True,
                "history": True, "stats": True, "trends": True,
                # теггер; сбор цифр (замеры, цифры с сайтов, сверка правок модераторов); новости; прогноз и лучшее время
                "tagger": True, "collect": True, "news": True, "forecast": True, "crosspost": True,
                # необязательные возможности поста, по умолчанию выключены — обычный пост без лишнего:
                # targeting — «Умное таргетирование» (версии файлов по площадкам), translation — английский текст, подписи и перевод
                "targeting": False, "translation": False},
    "light": {"scale": 0.5, "quality": 88, "subsampling": "4:4:4", "sharpen": False, "background": "#FFFFFF"},
    # готовые файлы (JPEG-превью, GIF, видео): своя папка (пусто — «Files» в каталоге программы), по датам, что сохранять
    "outputs": {"dir": "", "by_date": False, "jpeg": True, "gif": True, "video": True},
    "signatures": {
        # своя подпись для каждой площадки; ссылки можно писать как [Текст](https://…)
        # у каждой площадки: основная подпись (sfw / 18+) и английская (en) — она берётся, когда на площадку уходит английский текст
        "platforms": {k: {"sfw": "", "nsfw": "", "en": {"sfw": "", "nsfw": ""}} for k in ("telegram", "discord", "vk", "bluesky", "deviantart")},
        "use": {"telegram": True, "discord": True, "vk": True, "bluesky": True, "deviantart": True},
    },
    # на какие площадки уходит английский вариант текста поста (остальные — основной текст)
    "text_lang": {"telegram": "main", "discord": "main", "vk": "main", "bluesky": "main", "deviantart": "main", "e6ai": "main"},
    # какие версии файлов (комикс: ru / en / clean — без надписей) уходят на площадку; пусто — любые.
    # Работает только в постах, где включён переключатель «Умное таргетирование»
    "versions": {"telegram": [], "discord": [], "vk": [], "bluesky": [], "deviantart": [], "e6ai": []},
    # автоперевод описания (DeepSeek, OpenAI и любой сервис с интерфейсом OpenAI): текст уходит на их сервер, поэтому только по кнопке
    "translate": {"api_key": "", "model": "deepseek-flash", "base": "https://api.deepseek.com", "style": ""},
    "hashtags": {
        "enabled": True, "position": "start",         # start — первой строкой, как в ваших постах; end — в конце
        "rating_tags": {"sfw": "sfw", "erotic": "nsfw", "explicit": "nsfw"},
        "constant": [], "max_species": 3, "min_prob": 0.5,
        "use": {"telegram": True, "discord": True, "vk": True, "bluesky": True, "deviantart": False},
    },
    "telegram": {
        "enabled": False, "bot_token": "", "bot_name": "",
        "transport": "botapi",   # botapi — HTTPS к api.telegram.org; mtproto — как Telegram Desktop (нужны api_id/api_hash)
        "include_title": True, "spoiler_nsfw": True, "silent_originals": True, "comments_wait_seconds": 60,
        "originals_first": False,   # MTProto: не публиковать, пока PNG-оригиналы не загружены на сервер Telegram
        "preupload": True,       # MTProto: заливать PNG-оригиналы заранее, пока пишется пост
        "channels": [],          # [{"id", "title", "rating", "originals"}]
        "group": {"id": "", "title": "", "sfw_topic": 0, "nsfw_topic": 0, "sfw_topic_title": "", "nsfw_topic_title": ""},
    },
    "discord": {
        "enabled": False, "include_title": True, "blur_nsfw": True, "username": "", "max_file_mb": 10,
        "no_previews": True,     # без карточек-превью ссылок (картинки поста остаются)
        "short_links": True,     # «Текст: https://…» → кликабельное слово «Текст»
        "webhooks": [],          # [{"url", "name", "rating"}]
    },
    "e6ai": {
        "enabled": False, "username": "", "api_key": "", "base": "https://e6ai.net",
        "constant_tags": [],      # всегда добавляются: ваш director-тег, инструмент и т.п.
        "preselect": 0.5,         # теги теггера с уверенностью выше — отмечены сразу
        "show_min": 0.2,          # ниже — не показываем
        "with_sources": True, "with_description": True,
        "never_suggest": [],      # эти теги теггера не предлагать совсем
        # куда загружать: e6ai или e621 (движок и форма одни; аккаунт, ключ и постоянные теги — у каждого сайта свои)
        "site": "e6ai",
        "accounts": {"e621": {"username": "", "api_key": "", "constant_tags": []}},
    },
    "bluesky": {
        "enabled": False, "handle": "", "pds": "https://bsky.social",
        "rating": "all",         # sfw / nsfw / all — какие посты сюда уходят
        "include_title": True, "langs": ["ru"], "alt_from_title": True,
    },
    "deviantart": {
        "enabled": False, "client_id": "", "client_secret": "", "redirect_uri": "http://localhost:8765/callback",
        "rating": "all",         # откровенное сюда не уходит никогда — правила DeviantArt
        "is_ai_generated": True, "noai": True, "allow_comments": True, "allow_free_download": False,
        "gallery_id": "", "gallery_name": "", "originals": True,   # PNG-оригинал (до 30 МБ), иначе лёгкая версия
        "tags_from": "hashtags",  # hashtags — теги из хэштегов поста; none — без тегов
    },
    "pipeline": {
        "enabled": False,
        "per_day": 1,            # не больше стольких постов в сутки (по всем площадкам)
        "min_gap_h": 6,          # и не чаще, чем раз в столько часов
        "explore": 0.25,         # охота за новыми окнами: 0 — только лучшие известные, 0,5 — часто пробовать неизученные
        "horizon_days": 30,      # на сколько дней вперёд расставлять очередь
    },
    "vk": {
        "enabled": False, "group_id": "", "group_name": "", "app_id": "",
        "redirect_uri": "https://localhost/callback", "include_title": True,
        "access_token": "",      # готовый ключ пользователя (если вход через VK ID недоступен)
        "method": "browser",     # browser — через окно браузера; api — через VK ID / ключ
        "browser_ok": False, "show_window": True, "dry_shot": "", "dry_shot2": "",
        # статистика через браузер: страница «Статистика → Посты → Контент» и последняя разведка
        "stats_url": "", "recon_file": "", "recon_msg": "", "recon_at": "",
        "stats_auto": True, "stats_at": "", "stats_err": "", "stats_msg": "",   # сбор цифр постов раз в 3 часа
    },
}


def _path():
    return data_dir() / "publish.json"


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate(data: dict) -> dict:
    """Старая общая подпись (sfw/nsfw + discord_*) → своя у каждой площадки."""
    sg = data.get("signatures")
    if isinstance(sg, dict) and "platforms" not in sg and any(sg.get(k) for k in ("sfw", "nsfw", "discord_sfw", "discord_nsfw")):
        sfw, nsfw = sg.get("sfw", ""), sg.get("nsfw", "")
        sg["platforms"] = {
            "telegram": {"sfw": sfw, "nsfw": nsfw},
            "discord": {"sfw": sg.get("discord_sfw") or sfw, "nsfw": sg.get("discord_nsfw") or nsfw},
            "vk": {"sfw": sfw, "nsfw": nsfw},
        }
    if isinstance(sg, dict):
        for k in ("sfw", "nsfw", "discord_sfw", "discord_nsfw"):
            sg.pop(k, None)
    le = data.pop("light_export", None)   # прежняя «папка для копий JPEG» → общая папка готовых файлов
    if isinstance(le, dict) and le.get("dir") and "outputs" not in data:
        data["outputs"] = {"dir": le["dir"], "by_date": bool(le.get("by_date"))}
    return data


def load(raw: bool = False) -> dict:
    """Настройки. Без raw отключённые модули-площадки выглядят выключенными (enabled=False) — так их пропускают
    и публикация, и фоновая отправка, и цифры. Сохраняем всегда «сырые» (raw=True), чтобы ничего не потерять."""
    p = _path()
    from core.fsutil import read_json
    data = read_json(p)
    cfg = _merge(DEFAULTS, _migrate(data))
    if not raw:
        e6 = cfg["e6ai"]
        site = booru_site(cfg)
        if site != "e6ai":   # выбранный сайт — в те же поля, весь остальной код работает как с e6ai
            acc = (e6.get("accounts") or {}).get(site) or {}
            for k in PER_SITE:
                e6[k] = acc.get(k, [] if k == "constant_tags" else "")
            e6["base"] = BOORU[site]
        e6["site_name"] = site
        for k in PLATFORM_MODULES:
            if not cfg["modules"].get(k, True) and isinstance(cfg.get(k), dict):
                cfg[k]["enabled"] = False
        if not cfg["modules"].get("translation"):   # язык текста выключен: везде основной текст и основная подпись
            cfg["text_lang"] = {k: "main" for k in cfg["text_lang"]}
    return cfg


BOORU = {"e6ai": "https://e6ai.net", "e621": "https://e621.net"}
PER_SITE = ("username", "api_key", "constant_tags")   # своё у каждого сайта


def booru_site(cfg: dict | None = None) -> str:
    e6 = (cfg or load(raw=True))["e6ai"]
    return e6.get("site") if e6.get("site") in BOORU else "e6ai"


def site_changes(changes: dict) -> dict:
    """Правки раздела e6ai: аккаунт и постоянные теги — в ячейку выбранного сайта (e6ai — как раньше, сверху)."""
    site = booru_site()
    if site == "e6ai":
        return changes
    out = {k: v for k, v in changes.items() if k not in PER_SITE}
    mine = {k: v for k, v in changes.items() if k in PER_SITE}
    if mine:
        out["accounts"] = {site: mine}
    return out


def module_on(key: str) -> bool:
    """Включён ли модуль (площадка или экран) в «Настройки» → «Модули»."""
    try:
        return bool(load(raw=True)["modules"].get(key, True))
    except Exception:
        return True


def save(cfg: dict) -> dict:
    cfg = _merge(DEFAULTS, cfg)
    from core.fsutil import atomic_write
    atomic_write(_path(), json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def update(section: str, changes: dict) -> dict:
    cfg = load(raw=True)
    if section not in cfg:
        raise ValueError(f"Неизвестный раздел настроек: {section}")
    if isinstance(cfg[section], dict):
        for k, v in changes.items():
            if isinstance(v, dict) and isinstance(cfg[section].get(k), dict):
                cfg[section][k] = _merge(cfg[section][k], v)
            else:
                cfg[section][k] = v
    else:
        cfg[section] = changes
    return save(cfg)


def public(cfg: dict) -> dict:
    """Настройки для интерфейса: секреты не отдаём целиком."""
    out = copy.deepcopy(cfg)
    tok = out["telegram"].get("bot_token") or ""
    out["telegram"]["bot_token"] = ""
    out["telegram"]["has_token"] = bool(tok)
    out["vk"]["has_manual_token"] = bool(out["vk"].get("access_token"))
    out["e6ai"]["has_key"] = bool(out["e6ai"].get("api_key"))
    out["e6ai"]["api_key"] = ""
    for acc in (out["e6ai"].get("accounts") or {}).values():   # ключи второго сайта тоже не отдаём
        acc["has_key"] = bool(acc.get("api_key"))
        acc["api_key"] = ""
    out["vk"]["access_token"] = ""
    out["platform_order"] = platform_order(out)
    out["translate"]["has_key"] = bool(out["translate"].get("api_key"))
    from app import translate as _tr
    out["translate"]["ready"] = bool(out["translate"].get("api_key")) or _tr.is_local(out["translate"].get("base"))   # локальному серверу ключ не нужен
    out["translate"]["api_key"] = ""
    out["deviantart"]["has_secret"] = bool(out["deviantart"].get("client_secret"))
    out["deviantart"]["client_secret"] = ""
    return out
