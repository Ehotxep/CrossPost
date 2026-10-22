"""Общие вещи: конфиг, хранилище токенов, описание поста."""
import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKENS_PATH = ROOT / "tokens.json"


class PlatformError(Exception):
    """Понятная ошибка площадки, которую показываем пользователю."""


@dataclass
class Post:
    text: str = ""
    title: str = ""
    tags: list[str] = field(default_factory=list)
    mature: bool = False


# --- SFW / NSFW ---------------------------------------------------------------
# rating места публикации: "sfw" — только обычный контент, "nsfw" — только 18+, "all" — любой.
RATINGS = ("sfw", "nsfw", "all")

# Где перечислены отдельные места публикации внутри площадки
DEST_KEYS = {"discord": "webhook_urls", "telegram": ("channels", "chats"), "reddit": "subreddits"}
# Значение по умолчанию, если rating не указан. ВК — только SFW, на всякий случай.
DEFAULT_RATING = {"vk": "sfw"}


def rating_of(item, default: str = "all") -> str:
    r = (item.get("rating") if isinstance(item, dict) else None) or default
    r = str(r).lower()
    if r not in RATINGS:
        raise SystemExit(f"Неверный rating = {r!r} в config.toml (можно: sfw, nsfw, all)")
    return r


def allowed(rating: str, nsfw: bool) -> bool:
    return rating == "all" or (rating == "nsfw") == nsfw


def dest_label(item) -> str:
    if isinstance(item, str):
        return item if not item.startswith("https://discord.com/api/webhooks/") else "вебхук …" + item[-6:]
    for k in ("name", "id", "label"):
        if item.get(k):
            return str(item[k])
    if item.get("url"):
        return "вебхук …" + item["url"][-6:]
    return "?"


def apply_rating(name: str, c: dict, nsfw: bool) -> tuple[dict | None, list[str], list[str]]:
    """Возвращает (конфиг площадки с отфильтрованными местами или None, куда пойдёт, что пропущено)."""
    platform_rating = rating_of(c, DEFAULT_RATING.get(name, "all"))
    if not allowed(platform_rating, nsfw):
        return None, [], [f"вся площадка ({platform_rating})"]
    key = DEST_KEYS.get(name)
    if not key:
        return c, [], []
    if isinstance(key, tuple):  # несколько возможных названий (новое и старое)
        key = next((k for k in key if c.get(k)), key[0])
    items = list(c.get(key) or [])
    if name == "discord" and not items and c.get("webhook_url"):
        items = [c["webhook_url"]]
    keep = [i for i in items if allowed(rating_of(i, platform_rating), nsfw)]
    skipped = [f"{dest_label(i)} ({rating_of(i, platform_rating)})" for i in items if i not in keep]
    if not keep:
        return None, [], skipped or ["нет мест публикации"]
    return dict(c, **{key: keep}), [dest_label(i) for i in keep], skipped


def load_config(path: Path | None = None) -> dict:
    path = path or ROOT / "config.toml"
    if not path.exists():
        raise SystemExit(
            f"Не найден файл настроек {path}.\n"
            "Скопируйте config.example.toml в config.toml и заполните его."
        )
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_token(name: str) -> dict | None:
    if not TOKENS_PATH.exists():
        return None
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8")).get(name)


def save_token(name: str, data: dict) -> None:
    all_tokens = {}
    if TOKENS_PATH.exists():
        all_tokens = json.loads(TOKENS_PATH.read_text(encoding="utf-8"))
    data = dict(data, saved_at=int(time.time()))
    all_tokens[name] = data
    TOKENS_PATH.write_text(json.dumps(all_tokens, ensure_ascii=False, indent=2), encoding="utf-8")


def check_json(resp, platform: str) -> dict:
    """Разбирает JSON-ответ и превращает ошибки HTTP в PlatformError."""
    try:
        data = resp.json()
    except ValueError:
        raise PlatformError(f"{platform}: HTTP {resp.status_code}, ответ не JSON: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise PlatformError(f"{platform}: HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)[:500]}")
    return data
