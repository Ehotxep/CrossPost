"""Общие вещи: ошибка площадки, хранилище токенов, описание поста."""
import json
import threading
import time
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


def allowed(rating: str, nsfw: bool) -> bool:
    """Подходит ли место с рейтингом rating («sfw» / «nsfw» / «all») для поста: nsfw — это 18+."""
    return rating == "all" or (rating == "nsfw") == nsfw


_tok_lock = threading.RLock()


def load_token(name: str) -> dict | None:
    from core.fsutil import read_json
    with _tok_lock:
        return read_json(TOKENS_PATH).get(name)


def save_token(name: str, data: dict) -> None:
    """Токены разных площадок обновляются из разных потоков — читаем-меняем-пишем под замком, запись атомарная."""
    from core.fsutil import atomic_write, read_json
    with _tok_lock:
        all_tokens = read_json(TOKENS_PATH)
        all_tokens[name] = dict(data, saved_at=int(time.time()))
        atomic_write(TOKENS_PATH, json.dumps(all_tokens, ensure_ascii=False, indent=2))


def check_json(resp, platform: str) -> dict:
    """Разбирает JSON-ответ и превращает ошибки HTTP в PlatformError."""
    try:
        data = resp.json()
    except ValueError:
        raise PlatformError(f"{platform}: HTTP {resp.status_code}, ответ не JSON: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise PlatformError(f"{platform}: HTTP {resp.status_code}: {json.dumps(data, ensure_ascii=False)[:500]}")
    return data
