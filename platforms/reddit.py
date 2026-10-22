"""Reddit: пост-картинка (или галерея) в один или несколько сабреддитов.

Нужен доступ к API Reddit: с ноября 2025 новые приложения проходят ручное одобрение
(Responsible Builder Policy). Пока заявку не одобрили, держите enabled = false.
Используется библиотека PRAW и авторизация типа "script" (логин и пароль вашего аккаунта).
"""
import tempfile
import time
from pathlib import Path

from core.common import PlatformError

NAME = "Reddit"
MAX_IMAGES = 20


def limits(c: dict) -> dict:
    return {"max_bytes": 20 * 1024 * 1024, "formats": ("JPEG", "PNG", "GIF")}


def make_title(c: dict, p) -> str:
    title = p.title or (p.text.strip().splitlines() or [""])[0] or c.get("default_title", "")
    return title.strip()[:300]


def post(c: dict, images, p) -> list[str]:
    need = ["client_id", "client_secret", "username", "password"]
    missing = [k for k in need if not c.get(k)]
    if missing:
        raise PlatformError(f"Reddit: в config.toml не заполнено: {', '.join(missing)}")
    subs = c.get("subreddits") or []
    if not subs:
        raise PlatformError("Reddit: не указан ни один сабреддит в [[reddit.subreddits]]")
    title = make_title(c, p)
    if not title:
        raise PlatformError("Reddit: нужен заголовок — добавьте --title")
    # Если заголовок взят из первой строки текста, в комментарий она не повторяется
    comment = p.text.strip()
    if not p.title and comment.splitlines() and comment.splitlines()[0].strip()[:300] == title:
        comment = "\n".join(comment.splitlines()[1:]).strip()
    try:
        import praw
    except ImportError:
        raise PlatformError("Reddit: не установлена библиотека praw. Выполните: pip install praw")

    reddit = praw.Reddit(
        client_id=c["client_id"],
        client_secret=c["client_secret"],
        username=c["username"],
        password=c["password"],
        user_agent=c.get("user_agent") or f"crosspost-script/1.0 by u/{c['username']}",
    )

    results, errors = [], []
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, img in enumerate(images):
            path = Path(tmp) / f"{i}_{img.name}"
            path.write_bytes(img.data)
            paths.append(str(path))

        for n, sub in enumerate(subs):
            if isinstance(sub, str):
                sub = {"name": sub}
            name = sub["name"].removeprefix("r/")
            if n > 0:
                time.sleep(c.get("delay_seconds", 10))  # не спамим подряд — Reddit этого не любит
            try:
                sr = reddit.subreddit(name)
                kw = {"title": title,
                      "nsfw": bool(sub.get("nsfw", False) or p.mature or sub.get("rating") == "nsfw"),
                      "spoiler": bool(sub.get("spoiler", False))}
                if sub.get("flair_id"):
                    kw["flair_id"] = sub["flair_id"]
                if len(paths) == 1:
                    submission = sr.submit_image(image_path=paths[0], **kw)
                else:
                    submission = sr.submit_gallery(images=[{"image_path": pth} for pth in paths], **kw)
                link = f"https://www.reddit.com{submission.permalink}" if submission else f"r/{name} (отправлено)"
                if submission and comment and c.get("text_as_comment", True):
                    submission.reply(comment)
                results.append(link)
            except Exception as e:  # praw кидает много разных исключений
                errors.append(f"r/{name}: {type(e).__name__}: {e}")

    if errors:
        ok = f" (успешно: {', '.join(results)})" if results else ""
        raise PlatformError("Reddit: " + "; ".join(errors) + ok)
    return results
