"""X (Twitter): твит с картинками через официальный API.

Внимание: API X платный (оплата за каждый пост, порядка $0.015–0.02 за пост с картинкой,
а пост со ссылкой стоит ~$0.20). Поэтому по умолчанию ссылки из текста для X вырезаются.
"""
import re
from io import BytesIO

from core.common import PlatformError

NAME = "X"
MAX_IMAGES = 4
URL_RE = re.compile(r"https?://\S+|www\.\S+")


def limits(c: dict) -> dict:
    return {"max_bytes": 5 * 1024 * 1024, "formats": ("JPEG", "PNG", "WEBP", "GIF"), "max_side": 8192}


def _weight(ch: str) -> int:
    o = ord(ch)
    # Латиница, кириллица и т.п. считаются за 1 символ, эмодзи/иероглифы — за 2
    if o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037:
        return 1
    return 2


def fit_text(text: str, limit: int = 280) -> str:
    total, out = 0, []
    for ch in text:
        w = _weight(ch)
        if total + w > limit - 1:
            return "".join(out).rstrip() + "…"
        total += w
        out.append(ch)
    return text


def prepare_text(c: dict, p) -> tuple[str, bool]:
    text = p.text
    if p.title and c.get("include_title", False):
        text = f"{p.title}\n{text}".strip()
    stripped = False
    if not c.get("allow_links", False) and URL_RE.search(text):
        text = re.sub(r"[ \t]{2,}", " ", URL_RE.sub("", text)).strip()
        stripped = True
    return fit_text(text), stripped


def post(c: dict, images, p) -> list[str]:
    keys = ["api_key", "api_secret", "access_token", "access_token_secret"]
    missing = [k for k in keys if not c.get(k)]
    if missing:
        raise PlatformError(f"X: в config.toml не заполнено: {', '.join(missing)}")
    try:
        import tweepy
    except ImportError:
        raise PlatformError("X: не установлена библиотека tweepy. Выполните: pip install tweepy")

    auth = tweepy.OAuth1UserHandler(c["api_key"], c["api_secret"], c["access_token"], c["access_token_secret"])
    api = tweepy.API(auth)
    client = tweepy.Client(consumer_key=c["api_key"], consumer_secret=c["api_secret"],
                           access_token=c["access_token"], access_token_secret=c["access_token_secret"])
    text, _ = prepare_text(c, p)
    try:
        media_ids = [api.media_upload(filename=img.name, file=BytesIO(img.data)).media_id_string
                     for img in images]
        resp = client.create_tweet(text=text or None, media_ids=media_ids)
    except tweepy.TweepyException as e:
        raise PlatformError(f"X: {e}")
    return [f"https://x.com/i/status/{resp.data['id']}"]
