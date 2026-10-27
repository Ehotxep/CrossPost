"""Новый пост: план «куда уйдёт» и сама публикация с прогрессом по каждому месту.

Черновик поста (draft):
  files      — id файлов из uploads
  title, text
  rating     — "sfw" | "erotic" | "explicit"
  signature  — добавлять подпись (по умолчанию да)
  exclude    — ключи мест, выключенных вручную для этого поста
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import app.publish  # noqa: F401
import core.common as core_common
from app import db
from app.jobs import Cancelled, mask_secrets
from app.paths import data_dir
from app.publish import settings as S
from app.publish import e6ai as E6
from app.publish import hashtags as HT
from app.publish import prep, tg_bot, uploads
from core.common import PlatformError
from core.images import load_original
from platforms import discord as D
from platforms import telegram as T
from platforms import vk as V

core_common.TOKENS_PATH = data_dir() / "tokens.json"   # токены ВК и др. — в папке данных приложения

RATING_LABEL = {"sfw": "SFW", "erotic": "Эротика", "explicit": "Откровенное"}
MAX_IMAGES = 10

db.conn().executescript("""
CREATE TABLE IF NOT EXISTS publications (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    created  TEXT NOT NULL,
    title    TEXT, text TEXT, rating TEXT,
    files    TEXT,     -- JSON: [{id, name, kind}]
    dests    TEXT      -- JSON: состояние каждого места (ссылки, ошибки)
);
""")
try:   # черновик поста целиком — чтобы потом дослать его на другие площадки
    db.conn().execute("ALTER TABLE publications ADD COLUMN draft TEXT")
except Exception:  # noqa: BLE001 — колонка уже есть
    pass


def _pub_row(pub_id) -> dict | None:
    r = db.query("SELECT * FROM publications WHERE id=?", (int(pub_id),))
    return r[0] if r else None


def done_keys(pub_id) -> set:
    """Куда этот пост уже вышел."""
    row = _pub_row(pub_id)
    if not row:
        raise PlatformError("Публикация не найдена")
    return {d["key"] for d in json.loads(row["dests"] or "[]") if d.get("state") == "done"}


def extend_draft(pub_id) -> dict:
    """Черновик опубликованного поста — чтобы дослать его туда, куда он не ушёл (ошибка или площадка была выключена)."""
    row = _pub_row(pub_id)
    if not row:
        raise PlatformError("Публикация не найдена")
    try:
        d = json.loads(row.get("draft") or "null")
    except Exception:  # noqa: BLE001
        d = None
    if not isinstance(d, dict):   # опубликовано до этой версии — восстанавливаем, что можем
        d = {"title": row["title"] or "", "text": row["text"] or "", "rating": row["rating"], "signature": True,
             "exclude": ["e6ai"], "hashtags": None, "e6ai_tags": {}, "e6ai_meta": {},
             "files": [f["id"] if isinstance(f, dict) else f for f in json.loads(row["files"] or "[]")]}
    files, missing = [], []
    names = {f["id"]: f.get("name", "") for f in json.loads(row["files"] or "[]") if isinstance(f, dict)}
    for uid in d.get("files") or []:
        try:
            p = uploads.path_of(uid)
            info = uploads.describe(p)
            files.append({"id": uid, "name": info["name"], "kind": info["kind"], "width": info.get("width"), "height": info.get("height"),
                          "size": info["size"], "duration": info.get("duration"), "format": info.get("format", "")})
        except Exception:  # noqa: BLE001 — оригинал уже удалён (хранится неделю после публикации)
            missing.append(names.get(uid) or uid)
    dests = json.loads(row["dests"] or "[]")
    return {"pub_id": int(pub_id), "title": row["title"] or "", "files": files, "missing": missing,
            "done": [x["name"] for x in dests if x.get("state") == "done"],
            "failed": [x["name"] for x in dests if x.get("state") == "error"],
            "draft": {k: v for k, v in d.items() if k not in ("files", "extend", "_scheduled_run")}}


def _allowed(dest_rating: str, mature: bool) -> bool:
    return core_common.allowed(dest_rating or "all", mature)


def _rating_reason(dest_rating: str) -> str:
    return {"sfw": "здесь только SFW", "nsfw": "здесь только 18+"}.get(dest_rating, "")


def _files(draft) -> list[dict]:
    out = []
    for uid in draft.get("files") or []:
        p = uploads.path_of(uid)
        info = uploads.describe(p)
        out.append(dict(info, id=uid, path=p))
    return out


VERSIONS = ("ru", "en", "clean")   # метки версий файла; без метки (или «all») файл идёт на все площадки
VERSION_NAMES = {"ru": "RU", "en": "EN", "clean": "без надписей", "all": "для всех"}
MAX_FILES_MULTI = 40               # в режиме «несколько версий» файлов в посте может быть больше десяти


def select_ids(cfg, draft, ids, platform: str) -> list:
    """id файлов, которые уходят на площадку. Без переключателя «Умное таргетирование» или без выбранных в настройках
    версий — все файлы; иначе файлы без метки и файлы с меткой из набора площадки (порядок сохраняется)."""
    ids = list(ids)
    if not draft.get("multi") or not (cfg.get("modules") or {}).get("targeting"):
        return ids
    want = {v for v in ((cfg.get("versions") or {}).get(platform) or []) if v in VERSIONS}
    if not want:
        return ids
    marks = draft.get("marks") or {}
    return [i for i in ids if marks.get(i) not in VERSIONS or marks.get(i) in want]


def _vk_scope_ok(kind: str) -> bool:
    t = core_common.load_token("vk") or {}
    scope = str(t.get("scope") or "")
    if not scope:   # ВК не сообщил разрешения — пробуем
        return True
    return ("video" in scope) if kind == "video" else ("docs" in scope)


def _kind(files) -> str:
    kinds = {f["kind"] for f in files}
    if kinds <= {"image"}:
        return "images"
    if len(files) == 1:
        return files[0]["kind"]
    return "mixed"


def e6_tags(cfg, draft, file_id: str) -> list[str]:
    """Итоговые теги картинки для e6ai: постоянные из настроек + выбранные в посте."""
    chosen = ((draft.get("e6ai_tags") or {}).get(file_id)) or []
    from app import e6tags
    tags = E6.clean_tags(list(cfg["e6ai"].get("constant_tags") or []) + list(chosen))
    if e6tags.ready():   # старые названия → действующие (loona → loona_(helluva_boss)), без «родителей»
        tags = E6.clean_tags([i["name"] for i in e6tags.info(tags).values()])
        tags = e6tags.drop_implied(tags, keep=E6.clean_tags(cfg["e6ai"].get("constant_tags") or []))
    try:   # анимации e6ai требует помечать тегом animated
        if uploads.describe(uploads.path_of(file_id))["kind"] in ("gif", "video") and "animated" not in tags:
            tags.append("animated")
    except (ValueError, OSError):
        pass
    return tags


def has_en_signature(cfg, platform: str) -> bool:
    en = ((cfg["signatures"].get("platforms") or {}).get(platform) or {}).get("en") or {}
    return bool((en.get("sfw") or "").strip() or (en.get("nsfw") or "").strip())


def signature(cfg, platform: str, rating: str | None, lang: str = "main") -> str:
    """Подпись площадки. Для английского текста — английская; если её не заполняли совсем, остаётся основная."""
    sg = (cfg["signatures"].get("platforms") or {}).get(platform) or {}
    if lang == "en" and has_en_signature(cfg, platform):
        sg = sg.get("en") or {}
    return (sg.get("nsfw" if rating and rating != "sfw" else "sfw") or "").strip()


LANGS = ("main", "en")


def variant(cfg, draft, platform: str) -> tuple[str, str, str]:
    """(заголовок, текст, язык) для площадки: английский вариант, если площадка на него настроена и он заполнен, иначе основной."""
    lang = (cfg.get("text_lang") or {}).get(platform, "main")
    if lang in LANGS and lang != "main":
        v = (draft.get("variants") or {}).get(lang) or {}
        if (v.get("text") or "").strip() or (v.get("title") or "").strip():
            return (v.get("title") or "").strip(), (v.get("text") or "").strip(), lang
    return (draft.get("title") or "").strip(), (draft.get("text") or "").strip(), "main"


def title_for(cfg, draft, platform: str) -> str:
    return variant(cfg, draft, platform)[0]


def signed_text(cfg, draft, platform: str) -> str:
    """Текст поста + хэштеги + подпись этой площадки. Ссылки [Текст](url) каждая площадка оформит по-своему."""
    _, text, lang = variant(cfg, draft, platform)
    text = HT.apply(cfg, draft, text, platform)   # хэштеги для поиска (кроме e6ai)
    if draft.get("signature", True) and cfg["signatures"]["use"].get(platform, True):
        sig = signature(cfg, platform, draft.get("rating"), lang)
        if sig:
            text = f"{text}\n\n{sig}".strip()
    from app.publish import links
    return links.for_platform(text, platform)


# --- план -------------------------------------------------------------------------

def plan(draft: dict) -> dict:
    cfg = S.load()
    errors, notes = [], []
    all_files = files = _files(draft)
    multi = bool(draft.get("multi")) and bool(cfg["modules"].get("targeting"))   # «Умное таргетирование»: у каждой площадки свой набор файлов
    problems = {}
    rating = draft.get("rating")
    if not files:
        errors.append("Добавьте картинки, GIF или видео")
    if rating not in S.RATINGS:
        errors.append("Выберите рейтинг поста")
    kind = _kind(files) if files else "images"
    if kind == "mixed" and not multi:
        errors.append("GIF и видео публикуются по одному, отдельным постом — без картинок рядом")
    n_img = len(files) if kind == "images" else 0
    if n_img > MAX_IMAGES and not multi:
        errors.append(f"Не больше {MAX_IMAGES} картинок в одном посте")
    if multi and len(files) > MAX_FILES_MULTI:
        errors.append(f"Не больше {MAX_FILES_MULTI} файлов в одном посте")
    from app.publish import media as M
    anim = files[0] if kind in ("gif", "video") else None
    ainfo = (uploads.probe_cached(anim["path"]) if anim and anim["kind"] == "video" else {}) if anim else {}

    def setup(fl):
        nonlocal files, kind, n_img, anim, ainfo, what_anim
        files = fl
        kind = _kind(fl) if fl else "images"
        n_img = len(fl) if kind == "images" else 0
        anim = fl[0] if kind in ("gif", "video") else None
        ainfo = (uploads.probe_cached(anim["path"]) if anim and anim["kind"] == "video" else {}) if anim else {}
        what_anim = ("GIF" if kind == "gif" else "видео") + (f" {round(ainfo['duration'])} с" if ainfo.get("duration") else "")

    def use(platform):
        """Режим «Умное таргетирование»: дальше план считается по файлам, которые идут на эту площадку."""
        if not multi:
            return
        keep = set(select_ids(cfg, draft, [f["id"] for f in all_files], platform))
        setup([f for f in all_files if f["id"] in keep])
        if not all_files:
            return
        if not files:
            problems[platform] = "нет файлов с подходящей меткой версии — отметьте файлы или смените набор версий в «Настройки» → «Файлы» → «Умное таргетирование»"
        elif kind == "mixed":
            problems[platform] = "GIF и видео публикуются по одному, без картинок рядом"
        elif kind == "images" and n_img > MAX_IMAGES:
            problems[platform] = f"в этой версии {n_img} файлов, а в один пост помещается {MAX_IMAGES}"

    def media_need(platform, limit=None):
        """(можно ли, пояснение): файл как есть, переделка (нужен ffmpeg) или нельзя."""
        why = M.need_for(platform, anim, ainfo, limit)
        if why and not M.ready():
            return False, f"нужен ffmpeg ({why}) — «Настройки» → «Видео и GIF»"
        return True, why or ""
    what_anim = ("GIF" if kind == "gif" else "видео") + (f" {round(ainfo['duration'])} с" if ainfo.get("duration") else "")
    mature = rating in ("erotic", "explicit")
    off = set(draft.get("exclude") or [])
    done = set()
    if draft.get("extend"):   # досылка к уже опубликованному посту: где он уже вышел — не трогаем
        done = done_keys(draft["extend"])
        off |= done
    dests = []

    def add(key, platform, name, will, reason="", details=None, toggle=True, need=None):
        user_off = key in off
        if not files:
            details = []   # без файлов «0 фото» только путает
        want = (cfg.get("text_lang") or {}).get("telegram" if platform == "telegram" else platform, "main")
        if will and multi and all_files and platform in ("telegram", "discord", "vk", "bluesky", "deviantart", "e6ai"):
            vs = [VERSION_NAMES[v] for v in ((cfg.get("versions") or {}).get(platform) or []) if v in VERSION_NAMES]
            details = list(details or []) + [f"{len(files)} из {len(all_files)} файлов" + (f" · версии: {', '.join(vs)}" if vs else "")]
        if will and want != "main":   # площадка настроена на английский текст
            _, _, got = variant(cfg, draft, platform)
            details = list(details or []) + (["текст: English"] if got != "main" else ["⚠ английский текст не заполнен — уйдёт основной"])
            if (got != "main" and draft.get("signature", True) and cfg["signatures"]["use"].get(platform, True)
                    and signature(cfg, platform, rating) and not has_en_signature(cfg, platform)):
                details.append("подпись: основная (английской нет)")
        dests.append({"key": key, "platform": platform, "name": name, "will": will and not user_off,
                      "reason": "выключено для этого поста" if (will and user_off) else reason,
                      "details": details or [], "can_toggle": toggle and (will or bool(need)), "off": user_off,
                      "need": need or []})

    def what_tg():   # по файлам, которые идут в Telegram (в режиме «Умное таргетирование» они свои)
        return {"images": f"{n_img} фото JPEG" if n_img != 1 else "фото JPEG", "gif": what_anim, "video": what_anim}.get(kind, "") if files else ""
    tg_limit = None if cfg["telegram"].get("transport") == "mtproto" else tg_bot.VIDEO_LIMIT

    # Telegram
    use("telegram")
    tg = cfg["telegram"]
    configured = []
    if tg["enabled"] and tg["bot_token"] and (tg["channels"] or tg["group"].get("id")):
        configured.append("telegram")
        spoiler = mature and tg.get("spoiler_nsfw", True)
        published_src = []
        for i, ch in enumerate(tg["channels"]):
            key = f"telegram:{i}"
            name = f"Telegram · {ch.get('title') or ch['id']}"
            if not _allowed(ch.get("rating"), mature):
                add(key, "telegram", name, False, _rating_reason(ch.get("rating")), toggle=False)
                continue
            det = [what_tg() + (" под спойлером" if spoiler else "")]
            if anim:
                ok, why = media_need("telegram", tg_limit)
                if not ok:
                    add(key, "telegram", name, False, why, toggle=False)
                    continue
                if why:
                    det.append(why)
            if kind == "images" and ch.get("originals", "reply") != "none":
                det.append(f"{n_img} PNG-оригинал{'а' if 1 < n_img < 5 else 'ов' if n_img >= 5 else ''} "
                           + ("в комментарии" if ch.get("originals") == "comments" else "ответом на пост"))
            add(key, "telegram", name, True, details=det)
            if key not in off:
                published_src.append((key, ch))
        g = tg["group"]
        if g.get("id"):
            topic = g.get("nsfw_topic") if mature else g.get("sfw_topic")
            ttitle = (g.get("nsfw_topic_title") if mature else g.get("sfw_topic_title")) or (f"тема {topic}" if topic else "")
            name = f"Telegram · {g.get('title') or 'группа'}" + (f" → «{ttitle}»" if ttitle else "")
            want = "nsfw" if mature else "sfw"
            src = next((x for x in published_src if x[1].get("rating") == want), published_src[0] if published_src else None)
            if not topic:
                add("telegram:group", "telegram", name, False, f"тема для {'18+' if mature else 'SFW'} не указана", toggle=False)
            elif not src:
                add("telegram:group", "telegram", name, False, "не из чего пересылать — ни один канал не публикуется", toggle=False)
            else:
                add("telegram:group", "telegram", name, True,
                    details=[f"пересылка из «{src[1].get('title') or src[1]['id']}» без оригиналов"])
                dests[-1]["src"] = src[0]

    # Discord
    use("discord")
    dc = cfg["discord"]
    if dc["enabled"] and dc["webhooks"]:
        configured.append("discord")
        for i, w in enumerate(dc["webhooks"]):
            key = f"discord:{i}"
            name = f"Discord · {w.get('name') or 'вебхук ' + str(i + 1)}"
            if not _allowed(w.get("rating"), mature):
                add(key, "discord", name, False, _rating_reason(w.get("rating")), toggle=False)
            elif anim:
                ok, why = media_need("discord", int(dc.get("max_file_mb") or 10) * 1024 * 1024)
                if not ok:
                    add(key, "discord", name, False, why, toggle=False)
                else:
                    add(key, "discord", name, True, details=[what_anim + (" под спойлером" if mature and dc.get("blur_nsfw") else "")] + ([why] if why else []))
            else:
                add(key, "discord", name, True,
                    details=[f"{n_img} JPEG-превью" + (" под спойлером" if mature and dc.get("blur_nsfw") else "")])

    # ВКонтакте
    use("vk")
    vk = cfg["vk"]
    if vk["enabled"] and vk.get("group_id"):
        configured.append("vk")
        name = f"ВКонтакте · {vk.get('group_name') or 'группа ' + str(vk['group_id'])}"
        via_browser = vk.get("method") == "browser"
        if mature:
            add("vk", "vk", name, False, "ВК — только SFW", toggle=False)
        elif anim and via_browser and not vk.get("browser_ok"):
            add("vk", "vk", name, False, "не выполнен вход в браузере — «Настройки» → ВКонтакте", toggle=False)
        elif anim and via_browser:
            add("vk", "vk", name, True, details=[what_anim + (" (видеозаписью)" if kind == "video" else " (документом)") + " · через браузер"])
        elif anim and not vk.get("access_token") and not _vk_scope_ok(kind):
            add("vk", "vk", name, False, f"войдите во ВК заново — нужен доступ к {'видео' if kind == 'video' else 'документам (GIF)'}", toggle=False)
        elif anim and not (vk.get("access_token") or core_common.load_token("vk")):
            add("vk", "vk", name, False, "не выполнен вход — «Настройки» → ВКонтакте", toggle=False)
        elif anim:
            add("vk", "vk", name, True, details=[what_anim + (" (как видеозапись группы)" if kind == "video" else " (как документ — проигрывается в ленте)")])
        elif via_browser and not vk.get("browser_ok"):
            add("vk", "vk", name, False, "не выполнен вход в браузере — «Настройки» → ВКонтакте", toggle=False)
        elif not via_browser and not (vk.get("access_token") or core_common.load_token("vk")):
            add("vk", "vk", name, False, "не выполнен вход — «Настройки» → ВКонтакте", toggle=False)
        else:
            add("vk", "vk", name, True, details=[f"{n_img} JPEG-превью" + (" · через браузер" if via_browser else "")])

    # Bluesky
    use("bluesky")
    from platforms import bluesky as BS, deviantart as DA
    bs = cfg["bluesky"]
    bs_sess = core_common.load_token("bluesky") or {}
    if bs.get("enabled") and bs_sess.get("accessJwt"):
        configured.append("bluesky")
        name = f"Bluesky · @{bs_sess.get('handle') or bs.get('handle')}"
        if not _allowed(bs.get("rating"), mature):
            add("bluesky", "bluesky", name, False, _rating_reason(bs.get("rating")), toggle=False)
        elif anim:
            dur = ainfo.get("duration") or 0
            ok, why = media_need("bluesky", BS.VIDEO_MAX_BYTES)
            if kind == "video" and dur > BS.VIDEO_MAX_SECONDS:
                add("bluesky", "bluesky", name, False, "видео длиннее 3 минут — Bluesky такое не принимает", toggle=False)
            elif not ok:
                add("bluesky", "bluesky", name, False, why, toggle=False)
            else:
                add("bluesky", "bluesky", name, True, details=[what_anim + (" · 18+" if mature else "")] + ([why] if why else []))
        else:
            det = [f"{min(n_img, BS.MAX_IMAGES)} JPEG-превью" + (" · пометка 18+" if mature else "")]
            if n_img > BS.MAX_IMAGES:
                det.append(f"ещё {n_img - BS.MAX_IMAGES} не влезут — у Bluesky до 4 картинок")
            if files and BS._graphemes(BS.build_text(signed_text(cfg, draft, "bluesky"), 10 ** 6)[0]) > BS.TEXT_LIMIT:
                det.append("текст обрежется до 300 символов")
            add("bluesky", "bluesky", name, True, details=det)

    # DeviantArt
    use("deviantart")
    da = cfg["deviantart"]
    da_tok = core_common.load_token("deviantart") or {}
    if da.get("enabled") and da_tok.get("access_token"):
        configured.append("deviantart")
        name = f"DeviantArt · {da_tok.get('username') or 'аккаунт'}"
        if rating == "explicit":
            add("deviantart", "deviantart", name, False, "откровенное запрещено правилами DeviantArt", toggle=False)
        elif not _allowed(da.get("rating"), mature):
            add("deviantart", "deviantart", name, False, _rating_reason(da.get("rating")), toggle=False)
        elif kind == "video":
            add("deviantart", "deviantart", name, False, "DeviantArt не принимает видео", toggle=False)
        elif kind == "gif" and anim["size"] > DA.FILE_MAX_BYTES:
            add("deviantart", "deviantart", name, False, "GIF больше 30 МБ — DeviantArt не примет", toggle=False)
        else:
            n = len(files)
            what = "GIF" if kind == "gif" else (f"{n} работ{'а' if n == 1 else 'ы' if n < 5 else ''}" + (" PNG-оригиналами" if da.get("originals", True) else " JPEG-превью"))
            add("deviantart", "deviantart", name, True, details=[what + (" · Mature" if mature else "") + (" · отмечено как ИИ" if da.get("is_ai_generated", True) else "")])

    # e6ai — последним: в источники пойдут ссылки на уже вышедшие посты
    use("e6ai")
    e6 = cfg["e6ai"]
    if e6.get("enabled") and e6.get("username") and e6.get("api_key"):
        configured.append("e6ai")
        name = f"{E6.name(e6)} · {e6['username']}"
        ok6, why6 = media_need("e6ai") if anim else (True, "")
        if not ok6:
            add("e6ai", "e6ai", name, False, why6, toggle=False)
        elif files:
            from app.publish import e6check
            per = [e6_tags(cfg, draft, f["id"]) for f in files]
            metas = draft.get("e6ai_meta") or {}
            checks = [e6check.check((draft.get("e6ai_tags") or {}).get(f["id"]) or [],
                                    list(cfg["e6ai"].get("constant_tags") or []) + (["animated"] if f["kind"] in ("gif", "video") else []),
                                    metas.get(f["id"]), rating) for f in files]
            few = [f["name"] for f, t in zip(files, per) if len(t) < E6.MIN_TAGS]
            need = []
            legacy = "e6ai_meta" not in draft   # запланировано до появления проверки — как раньше
            for f, c in zip(files, checks):
                for b in ([] if legacy else e6check.blockers(c)):
                    label = b + (f" ({f['name']})" if len(files) > 1 else "")
                    if label not in need:
                        need.append(label)
            if few and not need:
                need.append(f"хотя бы {E6.MIN_TAGS} тега")
            if need:
                short = need if len(need) <= 4 else need[:4] + [f"ещё {len(need) - 4}"]
                add("e6ai", "e6ai", name, False, "нужно: " + ", ".join(short), need=need)
                if "e6ai" not in off and not draft.get("_scheduled_run"):   # в запланированном — просто пропустим e6ai
                    errors.append(f"{E6.name(e6)}: теги не готовы — нужно: " + ", ".join(short) + f". Заполните форму тегов или выключите {E6.name(e6)} для этого поста")
            else:
                n = len(files)
                add("e6ai", "e6ai", name, True, details=[
                    (what_anim + (f" · {why6}" if why6 else " как есть") if anim else
                     f"{n} оригинал{'' if n == 1 else 'а' if n < 5 else 'ов'} без сжатия" + (", связаны как родитель/дети" if n > 1 else "")),
                    f"рейтинг {E6.RATING.get(rating, '?')}", f"тегов: {min(c['total'] for c in checks)}" + (f"–{max(c['total'] for c in checks)}" if n > 1 else "")])
        else:
            add("e6ai", "e6ai", name, True)

    missing = [n for k, n in (("telegram", "Telegram"), ("discord", "Discord"), ("vk", "ВКонтакте"), ("bluesky", "Bluesky"),
                              ("deviantart", "DeviantArt"), ("e6ai", E6.name(cfg["e6ai"])))
               if k not in configured and cfg["modules"].get(k, True)]
    if not configured:
        errors.append("Ни одна площадка не настроена — откройте «Настройки» → «Публикация»")
    order = S.platform_order(cfg)   # площадки — в порядке, который выбрал пользователь (внутри площадки — как были)
    dests.sort(key=lambda d: order.index(d["platform"]) if d["platform"] in order else len(order))
    for d in dests:
        if d["will"] and d["platform"] in problems:
            d.update(will=False, reason=problems[d["platform"]], can_toggle=False, need=[])
    for d in dests:
        if d["key"] in done:
            d.update(will=False, off=False, can_toggle=False, need=[], done=True, reason="уже опубликовано")
    will = [d for d in dests if d["will"]]
    if done and files and not will:
        errors.append("Новых мест нет — включите площадку, куда дослать пост")
    elif files and rating in S.RATINGS and configured and not will:
        errors.append("С этим рейтингом пост никуда не уйдёт")
    return {"kind": _kind(all_files) if all_files else "images", "mature": mature, "rating": rating, "dests": dests, "errors": errors,
            "not_configured": missing, "configured": configured, "notes": notes,
            "texts": {p: signed_text(cfg, draft, p) for p in ("telegram", "discord", "vk", "bluesky", "deviantart")},
            # заголовок и язык текста каждой площадки: предпросмотр показывает ровно то, что уйдёт
            "titles": {p: title_for(cfg, draft, p) for p in ("telegram", "discord", "vk", "bluesky", "deviantart", "e6ai")},
            "langs": {p: variant(cfg, draft, p)[2] for p in ("telegram", "discord", "vk", "bluesky", "deviantart", "e6ai")},
            "hashtags": HT.final(cfg, draft), "hashtags_suggested": HT.suggest(cfg, draft),
            "hashtags_on": bool(cfg["hashtags"].get("enabled", True))}


# --- публикация ----------------------------------------------------------------------

_state_lock = threading.Lock()
_state: dict = {}   # текущая/последняя публикация — её показывает экран «Публикация»


def state() -> dict | None:
    with _state_lock:
        return json.loads(json.dumps(_state)) if _state else None


def _set(key=None, **kw):
    with _state_lock:
        if key is None:
            _state.update(kw)
        else:
            for d in _state["dests"]:
                if d["key"] == key:
                    d.update(kw)


def _save_pub():
    with _state_lock:
        st = json.loads(json.dumps(_state))
    with db.tx() as c:
        if st.get("pub_id"):
            c.execute("UPDATE publications SET dests=? WHERE id=?", (json.dumps(st["dests"], ensure_ascii=False), st["pub_id"]))
            if st.get("extend_draft"):   # досылка: запоминаем черновик с новыми площадками
                c.execute("UPDATE publications SET draft=? WHERE id=?", (json.dumps(st["extend_draft"], ensure_ascii=False), st["pub_id"]))
        else:
            cur = c.execute("INSERT INTO publications (created, title, text, rating, files, dests, draft) VALUES (?,?,?,?,?,?,?)",
                            (st["created"], st["title"], st["text"], st["rating"],
                             json.dumps(st["files"], ensure_ascii=False), json.dumps(st["dests"], ensure_ascii=False),
                             json.dumps(st.get("draft") or {}, ensure_ascii=False)))
            with _state_lock:
                _state["pub_id"] = cur.lastrowid


UNATTENDED_WAIT = 45 * 60   # публикация по расписанию без человека: повторы загрузки PNG не дольше 45 минут


NETFAIL = "Нет связи с api.telegram.org"
NETFAIL_TEXT = ("Публикация в Telegram не прошла: нет связи с api.telegram.org (проблема с соединением). "
                "Включите связь через MTProto или запас прокси в «Настройки» → «Дополнительно» → «Прокси и соединение».")


def net_failed(dests=None) -> bool:
    """Хоть одно место Telegram не опубликовано из-за недоступности api.telegram.org."""
    if dests is None:
        dests = (state() or {}).get("dests") or []
    return any(d.get("state") == "error" and NETFAIL in (d.get("message") or "") for d in dests)


def _notify_netfail():
    st = state() or {}
    if not net_failed(st.get("dests")):
        return
    try:
        from app.insights import news
        news._add(f"netfail:{st.get('pub_id')}", st.get("pub_id"), "telegram", "alert", NETFAIL_TEXT, level=3)
    except Exception:  # noqa: BLE001
        pass


def start(draft: dict, report) -> None:
    """Публикует черновик (запускается в фоне через Jobs)."""
    if draft.get("extend"):
        return extend(draft, report)
    p = plan(draft)
    if p["errors"]:
        raise PlatformError(p["errors"][0])
    files = _files(draft)
    dests = [dict(d, state="queued", message="", link="", note="") for d in p["dests"] if d["will"]]
    with _state_lock:
        _state.clear()
        _state.update({"pub_id": None, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "title": draft.get("title") or "", "text": draft.get("text") or "", "rating": draft["rating"],
                       "files": [{"id": f["id"], "name": f["name"], "kind": f["kind"]} for f in files],
                       "draft": draft, "dests": dests, "skipped": [d for d in p["dests"] if not d["will"]],
                       "running": True, "kind": p["kind"]})
    _save_pub()
    try:   # постоянная миниатюра — история не зависит от того, где лежат файлы
        from app.publish import storage
        storage.make_pub_thumb(state()["pub_id"], files)
    except Exception:
        from app.applog import log
        log("публикация: не удалось сохранить миниатюру")
    try:   # запоминаем прогноз — через неделю сравним с тем, что вышло
        from app.insights import forecast as FC, post_stats
        post_stats.save_forecasts(state()["pub_id"], FC.for_draft(draft)["items"])
    except Exception:
        from app.applog import log
        log("публикация: не удалось сохранить прогноз")
    _run(draft, report, only=None)


def extend(draft: dict, report) -> None:
    """Дослать опубликованный пост на площадки, куда он не ушёл. Остаётся ОДНА публикация: в истории и календаре —
    одна запись, у которой просто прибавились места."""
    pub_id = int(draft["extend"])
    row = _pub_row(pub_id)
    if not row:
        raise PlatformError("Публикация не найдена")
    draft = dict(draft, rating=row["rating"])   # рейтинг у поста один на всех площадках
    p = plan(draft)
    if p["errors"]:
        raise PlatformError(p["errors"][0])
    new = [dict(d, state="queued", message="", link="", note="") for d in p["dests"] if d["will"]]
    keys = {d["key"] for d in new}
    old = [d for d in json.loads(row["dests"] or "[]") if d["key"] not in keys]   # прежние ошибки на этих местах заменятся
    clean = {k: v for k, v in draft.items() if k != "extend"}
    with _state_lock:
        _state.clear()
        _state.update({"pub_id": pub_id, "created": row["created"], "title": row["title"] or "", "text": row["text"] or "",
                       "rating": row["rating"], "files": json.loads(row["files"] or "[]"), "draft": clean, "extend_draft": clean,
                       "dests": old + new, "skipped": [d for d in p["dests"] if not d["will"] and not d.get("done")],
                       "running": True, "kind": p["kind"], "extended": True})
    _save_pub()
    _run(clean, report, only=keys)


def _posted_but_failed(key: str, err) -> bool:
    """Пост в канал Telegram уже вышел (есть message_ids), а упало что-то после — например, загрузка PNG при обрыве связи.
    Место НЕ считается ошибкой: повторная публикация дала бы дубль. Остаётся «дослать PNG»."""
    if not key.startswith("telegram:") or key == "telegram:group":
        return False
    st = state() or {}
    d = next((x for x in st.get("dests", []) if x["key"] == key), None)
    if not d or not d.get("message_ids"):
        return False
    _set(key, state="done", message="", originals_pending=True,
         note=mask_secrets(f"⚠ пост вышел, но PNG не отправлены: {err} — их можно дослать кнопкой «Дослать PNG»"))
    return True


def _retry_originals(key, d, tg, ch, media, res, report, max_wait=None) -> dict:
    """Пост вышел, а PNG не дошли (обрыв, прокси): пробуем снова, сколько потребуется (пауза 2…60 с).
    Останавливает только «Отмена» — тогда остаётся кнопка «Дослать PNG». Нового поста не создаёт."""
    originals, ids = media.get("originals") or [], media.get("original_ids") or []
    attempt, t0 = 0, time.time()
    last_note, same = "", 0
    while True:
        if max_wait and time.time() - t0 > max_wait:   # без человека (по расписанию) очередь не держим вечно: PNG остаются «к досылке»
            return dict(res, originals_failed=True, note=(res.get("note") or "") + " — повторы остановлены, PNG можно дослать кнопкой «Дослать PNG»")
        attempt += 1
        pause = min(60, 2 ** min(attempt, 6))
        for left in range(pause, 0, -1):   # report каждую секунду — через него приходит «Отмена»
            _set(key, message=f"обрыв при загрузке PNG — попытка {attempt + 1} через {left} с…")
            report(message=f"{d['name']}: ждём перед повтором…")
            time.sleep(1)
        _set(key, message=f"догружаю PNG-оригиналы (попытка {attempt + 1})…")
        try:
            r = tg_bot.send_originals_only(tg, ch, originals, res["chat_id"], int(res["message_ids"][0]),
                                           lambda m, _k=key: _set(_k, message=m + "…"), ids)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "note": mask_secrets(f"⚠ PNG не отправлены: {e}")}
        if r["ok"]:
            return dict(res, note=r["note"], originals_failed=False)
        res = dict(res, note=r["note"])
        _set(key, note=r["note"])
        same = same + 1 if (r["note"] == last_note and not tg_bot.is_network_error(r["note"])) else 1
        last_note = r["note"]
        if same >= tg_bot.PERMANENT_AFTER:   # одна и та же не сетевая ошибка: Telegram отказывает, повтор не поможет
            return dict(res, originals_failed=True, note=r["note"] + " — это не обрыв связи, повторы остановлены; исправьте причину и нажмите «Дослать PNG»")


def _load_pub(pub_id: int) -> None:
    """Загружает опубликованный пост из истории в текущее состояние (после перезапуска программы)."""
    st = state()
    if st and st.get("pub_id") == int(pub_id):
        return
    row = _pub_row(pub_id)
    if not row:
        raise PlatformError("Публикация не найдена")
    try:
        draft = json.loads(row.get("draft") or "null")
    except Exception:  # noqa: BLE001
        draft = None
    with _state_lock:
        _state.clear()
        _state.update({"pub_id": int(pub_id), "created": row["created"], "title": row["title"] or "", "text": row["text"] or "",
                       "rating": row["rating"], "files": json.loads(row["files"] or "[]"), "draft": draft or {},
                       "dests": json.loads(row["dests"] or "[]"), "skipped": [], "running": False, "kind": ""})


def resend_originals(pub_id: int, report) -> None:
    """Дослать PNG-оригиналы к уже вышедшим постам Telegram. Новых постов не создаёт."""
    _load_pub(pub_id)
    st = state()
    cfg = S.load()
    tg = cfg["telegram"]
    todo = [d for d in st["dests"] if d.get("originals_pending") and d.get("message_ids")]
    if not todo:
        raise PlatformError("Досылать нечего — все PNG уже отправлены")
    draft = st.get("draft") or {}
    ids = [f["id"] if isinstance(f, dict) else f for f in (draft.get("files") or st.get("files") or [])]
    ids = select_ids(cfg, draft, ids, "telegram")   # в режиме «Умное таргетирование» PNG только тех файлов, что ушли в Telegram
    originals, oids, lost = [], [], []
    for uid in ids:
        try:
            originals.append(load_original(uploads.path_of(uid), T.BOT_FILE_LIMIT))
            oids.append(uid)
        except Exception:  # noqa: BLE001 — оригинал уже удалён (хранится неделю)
            lost.append(uid)
    if not originals:
        raise PlatformError("Файлы поста уже удалены (они хранятся неделю после публикации) — PNG придётся добавить вручную")
    _set(running=True)
    for i, d in enumerate(todo):
        key = d["key"]
        _set(key, message="догружаю PNG-оригиналы…", progress=0.0)
        report(message=f"{d['name']}: PNG…", progress=i / len(todo))
        try:
            chs = tg["channels"]
            ch = chs[int(key.split(":")[1])]
            res = tg_bot.send_originals_only(tg, ch, originals, d["chat_id"], int(d["message_ids"][0]),
                                             lambda m, _k=key: _set(_k, message=m + "…"), oids)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "note": mask_secrets(f"⚠ PNG не отправлены: {e}")}
        if res["ok"]:
            note = res["note"] + (f" · {len(lost)} файл(ов) уже удалено, их PNG нет" if lost else "")
            _set(key, message="", originals_pending=False, note=note, progress=1.0)
        else:
            _set(key, message="", note=res["note"] + " — можно попробовать ещё раз")
        _save_pub()
    _set(running=False)
    _save_pub()
    report(progress=1.0, message="PNG досланы")


def retry(report) -> None:
    st = state()
    if not st:
        raise PlatformError("Нечего повторять")
    for d in st["dests"]:   # пост уже вышел — повторять пост нельзя, только PNG
        if d["state"] == "error" and d.get("message_ids") and d["key"].startswith("telegram:") and d["key"] != "telegram:group":
            _set(d["key"], state="done", message="", originals_pending=True)
    st = state()
    failed = [d["key"] for d in st["dests"] if d["state"] == "error"]
    if not failed:
        if any(d.get("originals_pending") for d in st["dests"]):
            return resend_originals(st["pub_id"], report)
        raise PlatformError("Неудачных мест нет")
    for k in failed:
        _set(k, state="queued", message="", note="")
    _set(running=True)
    _run(st["draft"], report, only=set(failed))


def _run(draft, report, only):
    cfg = S.load()
    st = state()
    mature = draft["rating"] != "sfw"
    all_files = _files(draft)

    def _sel(platform):
        """Файлы и их вид (images/gif/video) для площадки — с учётом меток версий («Умное таргетирование»)."""
        keep = set(select_ids(cfg, draft, [f["id"] for f in all_files], platform))
        fl = [f for f in all_files if f["id"] in keep]
        return fl, (_kind(fl) if fl else "images")
    todo = [d for d in st["dests"] if only is None or d["key"] in only]
    total = max(len(todo), 1)
    media_cache = {}

    media_lock = threading.Lock()

    def media_for(platform, log=lambda m: None):
        with media_lock:
            return _media_for(platform, log)

    def anim_file(platform, limit=None, log=lambda m: None):
        """GIF/видео для площадки: как есть или переделанный (только если иначе не примет)."""
        from app.publish import media as M
        f = _sel(platform)[0][0]
        info = uploads.probe_cached(f["path"]) if f["kind"] == "video" else {}
        if not M.need_for(platform, f, info, limit):
            return f["path"], f["kind"], info
        if platform == "e6ai":
            return M.to_webm(f["id"], f["path"], log), "video", info
        big = limit and f["size"] > limit
        out = M.to_mp4(f["id"], f["path"], limit if big else None, log)
        return out, "video", M.probe(out) or info

    def _media_for(platform, log):
        if platform in media_cache:
            return media_cache[platform]
        files, kind = _sel(platform)
        light = cfg["light"]
        if platform == "telegram" and kind != "images":
            lim = None if cfg["telegram"].get("transport") == "mtproto" else tg_bot.VIDEO_LIMIT
            path, k, info = anim_file("telegram", lim, log)
            m = {"kind": k, "file": path, "info": info, "name": Path(files[0]["name"]).stem + path.suffix}
        elif platform == "discord" and kind != "images":
            path, k, info = anim_file("discord", int(cfg["discord"].get("max_file_mb") or 10) * 1024 * 1024, log)
            m = [_Blob(Path(files[0]["name"]).stem + path.suffix, path.read_bytes(), "image/gif" if path.suffix.lower() == ".gif" else
                       "video/webm" if path.suffix.lower() == ".webm" else "video/mp4")]
        elif platform == "bluesky":
            from platforms import bluesky as BS
            m = [prep.get_light(f["id"], f["path"], light, max_bytes=BS.IMAGE_MAX_BYTES) for f in files[:BS.MAX_IMAGES]]
        elif platform == "telegram":
            if kind == "images":
                m = {"kind": "images",
                     "photos": [prep.get_light(f["id"], f["path"], light, max_bytes=T.PHOTO_MAX_BYTES, max_sum=T.PHOTO_MAX_SUM) for f in files]}
                if any(ch.get("originals", "reply") != "none" for ch in cfg["telegram"]["channels"]):
                    m["originals"] = [load_original(f["path"], T.BOT_FILE_LIMIT) for f in files]
                    m["original_ids"] = [f["id"] for f in files]   # по ним найдутся залитые заранее (MTProto)
            else:
                m = {"kind": kind, "file": files[0]["path"]}
        elif platform == "discord":
            mb = int(cfg["discord"].get("max_file_mb") or 10)
            m = [prep.get_light(f["id"], f["path"], light, max_bytes=mb * 1024 * 1024) for f in files]
        else:  # vk
            m = [prep.get_light(f["id"], f["path"], light, max_bytes=50 * 1024 * 1024, max_sum=14000) for f in files]
        media_cache[platform] = m
        return m

    def one(n, d):
            key = d["key"]
            files, kind = _sel(d.get("platform") or key.split(":")[0])
            _set(key, state="running", message="готовлю файлы…", progress=0.0)

            def prog(frac, _key=key):
                frac = max(0.0, min(1.0, float(frac)))
                _set(_key, progress=frac)
                cur = state()["dests"]
                try:
                    report(progress=sum((1.0 if x["state"] in ("done", "error") else float(x.get("progress") or 0)) for x in cur if x["key"] in keys) / total)
                except Cancelled:   # прогресс зовут из потоков загрузки (asyncio): туда BaseException пускать нельзя
                    raise _Stop("отменено")

            def log(msg, _key=key):   # шаги публикации в Telegram → и текст, и примерный прогресс
                _set(_key, message=msg + "…")
                for word, frac in (("отправляю", 0.1), ("жду копию", 0.65), ("кладу PNG", 0.7)):
                    if msg.startswith(word):
                        prog(frac)
            from core import retry as _retry
            _retry.set_cancel_check(lambda: report())   # повторы загрузок внутри площадок останавливает «Отмена»
            try:
                report(message=f"{d['name']}…")   # здесь же останавливает «Отмена», пока место ещё не началось
                if not files:
                    raise PlatformError("для этой площадки нет файлов с подходящей меткой версии")
                if kind == "mixed":
                    raise PlatformError("GIF и видео публикуются по одному, без картинок рядом")
                if key.startswith("telegram:") and key != "telegram:group":
                    tg = cfg["telegram"]
                    ch = tg["channels"][int(key.split(":")[1])]
                    def on_posted(out, _key=key):   # пост вышел — ссылка сразу, оригиналы догружаются следом
                        _set(_key, link=out["link"], chat_id=out["chat_id"], message_ids=out["message_ids"],
                             message="пост вышел, догружаю PNG-оригиналы…")
                        posted[_key].set()
                    try:
                        res = tg_bot.publish_channel(tg, ch, media_for("telegram", lambda m: _set(key, message=m + "…")), signed_text(cfg, draft, "telegram"),
                                                     title_for(cfg, draft, "telegram"), mature, log, prog, on_posted)
                    except (Cancelled, _Stop):
                        raise
                    except Exception as e:  # noqa: BLE001 — пост уже вышел, упали оригиналы: не ошибка, а «досылаем»
                        cur = next((x for x in state()["dests"] if x["key"] == key), {})
                        if not cur.get("message_ids"):
                            raise
                        res = {"link": cur["link"], "chat_id": cur["chat_id"], "message_ids": cur["message_ids"],
                               "note": mask_secrets(f"⚠ пост вышел, но PNG не отправлены: {e}"), "originals_failed": True}
                    if res.get("originals_failed"):
                        res = _retry_originals(key, d, tg, ch, media_for("telegram"), res, report,
                                               max_wait=UNATTENDED_WAIT if draft.get("_scheduled_run") else None)
                    _set(key, state="done", message="", link=res["link"], note=res["note"],
                         chat_id=res["chat_id"], message_ids=res["message_ids"],
                         originals_pending=bool(res.get("originals_failed")))
                elif key == "telegram:group":
                    if d.get("src") in posted:   # ждём только сам пост в канале, не оригиналы
                        _set(key, message="жду пост в канале…")
                        posted[d["src"]].wait(1800)
                    cur = state()
                    src = next((x for x in cur["dests"] if x["key"] == d.get("src")), None)
                    if not src or not src.get("message_ids"):
                        raise PlatformError("пост в канал-источник не вышел — пересылать нечего")
                    g = cfg["telegram"]["group"]
                    topic = g.get("nsfw_topic") if mature else g.get("sfw_topic")
                    _set(key, message="пересылаю…"); prog(0.5)
                    link = tg_bot.forward(cfg["telegram"], g["id"], int(topic), src["chat_id"], src["message_ids"])
                    _set(key, state="done", message="", link=link)
                elif key.startswith("discord:"):
                    dc = cfg["discord"]
                    w = dc["webhooks"][int(key.split(":")[1])]
                    post = core_common.Post(text=signed_text(cfg, draft, "discord"), title=title_for(cfg, draft, "discord"), mature=mature)
                    media = media_for("discord", lambda m: _set(key, message=m + "…"))
                    _set(key, message="отправляю…"); prog(0.3)
                    D.post(dict(dc, webhook_urls=[w["url"]]), media, post)
                    _set(key, state="done", message="", note="отправлено в канал")
                elif key == "e6ai":
                    e6 = cfg["e6ai"]
                    cur = state()
                    sources = []
                    if e6.get("with_sources", True):   # публичные ссылки на этот пост в других местах
                        sources = [x["link"] for x in cur["dests"] if x["state"] == "done" and str(x.get("link", "")).startswith("http")
                                   and "/c/" not in x["link"] and x["key"] != "e6ai"]
                    desc = ""
                    if e6.get("with_description", True):
                        e_title, t, _ = variant(cfg, draft, "e6ai")
                        desc = ((f"[b]{e_title}[/b]\n" if e_title else "") + t).strip()
                    parent, first, dup = None, None, 0
                    for i, f in enumerate(files):
                        _set(key, message=f"загружаю оригинал {i + 1} из {len(files)}…")
                        tags6 = e6_tags(cfg, draft, f["id"])
                        if kind in ("gif", "video"):
                            path, _, _ = anim_file("e6ai", log=lambda m: _set(key, message=m + "…"))
                            _set(key, message=f"загружаю на {E6.name(e6)}…")
                            try:
                                res = E6.upload(e6, str(path), tags6, draft["rating"], sources, desc, parent, Path(f["name"]).stem + path.suffix)
                            except PlatformError as err:   # e6ai не принял формат — пробуем WebM
                                from app.publish import media as M
                                if not (E6.bad_format(str(err)) and path.suffix.lower() != ".webm" and M.ready()):
                                    raise
                                path = M.to_webm(f["id"], f["path"], lambda m: _set(key, message=m + "…"))
                                _set(key, message=f"загружаю WebM на {E6.name(e6)}…")
                                res = E6.upload(e6, str(path), tags6, draft["rating"], sources, desc, parent, Path(f["name"]).stem + ".webm")
                        else:
                            res = E6.upload(e6, str(f["path"]), tags6, draft["rating"], sources, desc, parent)
                        if not res.get("duplicate"):   # через сутки сверим с правками модераторов
                            try:
                                from app.insights import e6learn
                                e6learn.record(state()["pub_id"], res["post_id"], tags6 if kind in ("gif", "video") else e6_tags(cfg, draft, f["id"]), E6.name(cfg["e6ai"]))
                            except Exception:
                                from app.applog import log
                                log("e6ai: не удалось запомнить загрузку для сверки правок")
                        parent = parent or res["post_id"]
                        first = first or res["url"]
                        dup += res["duplicate"]
                        prog((i + 1) / len(files))
                    _set(key, state="done", message="", link=first or "",
                         note=(f"{len(files)} {'поста' if len(files) < 5 else 'постов'}, связаны как родитель/дети" if len(files) > 1 else "") + (f"; уже были на {E6.name(e6)}: {dup}" if dup else ""))
                elif key == "bluesky":
                    from platforms import bluesky as BS
                    bs = cfg["bluesky"]
                    text = signed_text(cfg, draft, "bluesky")
                    if title_for(cfg, draft, "bluesky") and bs.get("include_title", True):
                        text = f"{title_for(cfg, draft, 'bluesky')}\n\n{text}".strip()
                    alt = title_for(cfg, draft, "bluesky") if bs.get("alt_from_title", True) else ""
                    if kind == "images":
                        imgs = media_for("bluesky")
                        _set(key, message="загружаю картинки…"); prog(0.3)
                        res = BS.post(bs, text, draft["rating"], images=imgs, alt=alt, langs=bs.get("langs"))
                    else:
                        path, _, info = anim_file("bluesky", BS.VIDEO_MAX_BYTES, lambda m: _set(key, message=m + "…"))
                        _set(key, message="загружаю видео…"); prog(0.4)
                        res = BS.post(bs, text, draft["rating"], video={"data": path.read_bytes(), "width": info.get("width"),
                                                                       "height": info.get("height")}, alt=alt, langs=bs.get("langs"))
                    _set(key, state="done", message="", link=res["link"], uri=res["uri"],
                         note="Bluesky обрабатывает видео — в ленте оно появится через минуту" if kind != "images" else "")
                elif key == "deviantart":
                    from platforms import deviantart as DA
                    da = cfg["deviantart"]
                    up = []
                    for f in files:
                        if kind == "images" and not (da.get("originals", True) and f["size"] <= DA.FILE_MAX_BYTES):
                            im = prep.get_light(f["id"], f["path"], cfg["light"], max_bytes=DA.FILE_MAX_BYTES)
                            up.append((Path(f["name"]).stem + ".jpg", im.data, im.mime))
                        else:
                            ext = Path(f["name"]).suffix.lower()
                            up.append((f["name"], Path(f["path"]).read_bytes(),
                                       {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(ext, "image/jpeg")))
                    tags = HT.final(cfg, draft) if da.get("tags_from", "hashtags") == "hashtags" else []
                    tags = [t for t in tags if t.lower() not in ("sfw", "nsfw")]
                    _set(key, message="загружаю на DeviantArt…"); prog(0.1)
                    urls = DA.publish(da, up, title_for(cfg, draft, "deviantart"), signed_text(cfg, draft, "deviantart"), tags,
                                      draft["rating"], progress=prog)
                    _set(key, state="done", message="", link=next((u["url"] for u in urls if u["url"]), ""),
                         deviation_ids=[u["id"] for u in urls if u["id"]], note=f"{len(urls)} работ{'ы' if 1 < len(urls) < 5 else ''}" if len(urls) > 1 else "")
                elif key == "vk":
                    vk = cfg["vk"]
                    post = core_common.Post(text=signed_text(cfg, draft, "vk"), title=title_for(cfg, draft, "vk"), mature=False)
                    if kind in ("gif", "video") and vk.get("method") == "browser":
                        from app.publish import vk_web
                        text = post.text
                        if post.title and vk.get("include_title", True):
                            text = f"{post.title}\n\n{text}".strip()
                        res = vk_web.post(vk["group_id"], [str(files[0]["path"])], text, headless=not vk.get("show_window", True),
                                          progress=prog, log=lambda m: _set(key, message=m + "…"), kind=kind)
                        _set(key, state="done", message="", link=res.get("link", ""), note=res.get("note", ""))
                        raise _Done()
                    if kind in ("gif", "video"):
                        _set(key, message="загружаю " + ("видео" if kind == "video" else "GIF") + "…")
                        links = V.post_media(dict(vk), files[0]["path"], kind, post, progress=prog)
                        _set(key, state="done", message="", link=links[0] if links else "",
                             note="ВК ещё обрабатывает видео — в ленте оно появится через пару минут" if kind == "video" else "")
                        raise _Done()
                    media = media_for("vk")
                    if vk.get("method") == "browser":
                        from app.publish import vk_web
                        from app.paths import sub
                        paths = []
                        for i, m in enumerate(media):
                            fp = sub("tmp") / f"vk_{int(time.time())}_{i}.jpg"
                            fp.write_bytes(m.data)
                            paths.append(str(fp))
                        text = post.text
                        if post.title and vk.get("include_title", True):
                            text = f"{post.title}\n\n{text}".strip()
                        try:
                            res = vk_web.post(vk["group_id"], paths, text, headless=not vk.get("show_window", True),
                                              progress=prog, log=lambda m: _set(key, message=m + "…"))
                        finally:
                            for fp in paths:
                                try:
                                    os.remove(fp)
                                except OSError:
                                    pass
                        _set(key, state="done", message="", link=res.get("link", ""), note=res.get("note", ""))
                    else:
                        _set(key, message="загружаю фото…")
                        links = V.post(dict(vk), media, post, progress=prog)
                        _set(key, state="done", message="", link=links[0] if links else "")
            except _Done:
                _set(key, progress=1.0)
            except (Cancelled, _Stop):   # «Отмена»: если пост уже вышел — остаётся «дослать PNG», иначе место просто не отправлено
                if not _posted_but_failed(key, "отменено"):
                    _set(key, state="error", message="Отменено — не отправлено")
            except (PlatformError, ValueError, OSError, RuntimeError) as e:
                if not _posted_but_failed(key, e):
                    _set(key, state="error", message=_friendly(key, mask_secrets(str(e))))
            except Exception as e:  # noqa: BLE001 — сеть и т.п.: не роняем остальные места
                if not _posted_but_failed(key, e):
                    _set(key, state="error", message=mask_secrets(f"{type(e).__name__}: {e}"))
            else:
                _set(key, progress=1.0)
            if key in posted:
                posted[key].set()   # и при ошибке — чтобы пересылка в группу не ждала вечно
            _save_pub()

    # Режим «публиковать после загрузки оригиналов»: сначала PNG уходят на сервер Telegram, и только потом — всё остальное
    tgc = cfg["telegram"]
    if (tgc.get("originals_first") and tgc.get("transport") == "mtproto" and _sel("telegram")[1] == "images"
            and any(d["key"].startswith("telegram:") and d["key"] != "telegram:group" for d in todo)
            and any(c.get("originals", "reply") != "none" for c in tgc["channels"])):
        for d in todo:
            _set(d["key"], message="жду, пока загрузятся PNG-оригиналы…")
        try:
            med = media_for("telegram")
            report(message="Загружаю PNG-оригиналы в Telegram…")
            tg_bot.wait_originals(tgc, med["originals"], med["original_ids"], UNATTENDED_WAIT if draft.get("_scheduled_run") else None,
                                  lambda f, msg: report(progress=0.5 * f, message=msg))
        except PlatformError as e:   # без оригиналов пост в Telegram не выходит; остальные площадки идут как обычно
            for d in list(todo):
                if d["key"].startswith("telegram:"):
                    _set(d["key"], state="error", message=mask_secrets(str(e)))
                    todo.remove(d)
            _save_pub()
        except Cancelled:
            for d in todo:
                _set(d["key"], state="error", message="Отменено — не отправлено")
            _set(running=False)
            _save_pub()
            raise
        for d in todo:
            _set(d["key"], message="")

    # Порядок: все лёгкие посты сразу и параллельно (каналы Telegram, Discord, ВК), PNG-оригиналы в Telegram
    # догружаются следом внутри своих потоков, пересылка в группу ждёт только сам пост, e6ai — последним
    # (ему нужны ссылки на вышедшие посты для источников).
    from concurrent.futures import ThreadPoolExecutor
    keys = {d["key"] for d in todo}
    posted = {d["key"]: threading.Event() for d in todo if d["key"].startswith("telegram:") and d["key"] != "telegram:group"}
    first_wave = [(n, d) for n, d in enumerate(todo) if d["key"] != "e6ai"]
    last = [(n, d) for n, d in enumerate(todo) if d["key"] == "e6ai"]
    if first_wave:
        with ThreadPoolExecutor(max_workers=len(first_wave), thread_name_prefix="publish") as ex:
            list(ex.map(lambda nd: one(*nd), first_wave))
    for n, d in last:
        one(n, d)
    _set(running=False)
    _save_pub()
    st = state()
    ok = sum(1 for d in st["dests"] if d["state"] == "done")
    bad = sum(1 for d in st["dests"] if d["state"] == "error")
    _notify_netfail()
    report(progress=1.0, message=f"опубликовано: {ok}" + (f", ошибок: {bad}" if bad else "") + (" — нет связи с api.telegram.org" if net_failed(st["dests"]) else ""))


class _Stop(Exception):
    """Отмена, пришедшая изнутри загрузки (обычное исключение — asyncio его переживает)."""


class _Done(Exception):
    """Место отработало раньше конца своей ветки."""


class _Blob:
    def __init__(self, name, data, mime):
        self.name, self.data, self.mime = name, data, mime


def _friendly(key: str, msg: str) -> str:
    if key == "vk" and ("scope" in msg or "no access to call this method" in msg):
        return ("У ключа ВК нет разрешения на фото и стену. В кабинете VK ID откройте ваше приложение → «Доступы» и включите "
                "«Фотографии», «Стена» и «Сообщества». Потом в Crosspost: «Настройки» → ВКонтакте → «Выйти» и войдите заново.")
    if key.startswith("telegram") and "Нет связи с api.telegram.org" in msg:
        return msg + " — переключите связь с Telegram на MTProto в «Настройках» (как у Telegram Desktop)."
    if key.startswith("discord:"):
        if "HTTP 404" in msg:
            return "Вебхук не найден — его удалили в Discord. Создайте новый и замените в «Настройках»."
        if "HTTP 413" in msg or "Request entity too large" in msg:
            return "Файл слишком большой для этого сервера Discord — уменьшите лимит размера в «Настройках»."
        if "HTTP 403" in msg:
            return "Discord отказал в доступе: проверьте, что вебхук не отключён и у него есть права на канал."
    return msg


def history(limit: int = 50) -> list[dict]:
    rows = db.query("SELECT * FROM publications ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["files"] = json.loads(r["files"] or "[]")
        r["dests"] = json.loads(r["dests"] or "[]")
        r.pop("draft", None)
    return rows
