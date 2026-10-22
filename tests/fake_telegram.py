"""Имитация Telegram для проверки входа, импорта и статистики без настоящего аккаунта.

Заложено: канал растёт со временем; вечером (18–21) посты набирают больше; часть постов — альбомы,
часть — текст и пересылки (они не должны попасть в статистику); есть несколько «хитов».
Код входа 12345, облачный пароль pw.
"""
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from app.telegram.backend import AuthError, NeedPassword

NOW = datetime.now(timezone.utc)


def _make_channel(chat_id, n_posts, seed, base_views):
    rnd = random.Random(seed)
    msgs, mid = [], 1
    start = NOW - timedelta(days=900)
    for k in range(n_posts):
        t = start + timedelta(days=900 * k / n_posts, hours=rnd.choice([9, 12, 14, 19, 20, 21, 23]), minutes=rnd.randint(0, 59))
        age = (t - start).days / 900
        grow = 0.3 + 1.7 * age                                      # канал растёт
        evening = 1.5 if 18 <= t.astimezone().hour <= 21 else 1.0   # вечером заходит лучше
        hit = 3.0 if rnd.random() < 0.05 else 1.0
        views = int(base_views * grow * evening * hit * rnd.uniform(0.75, 1.25))
        kind = rnd.random()
        if kind < 0.08:   # текстовый пост
            msgs.append(dict(msg_id=mid, date=t.isoformat(), text="Новости канала", grouped_id=None, media_type="text",
                             views=views, forwards=1, replies=0, reactions=2, is_forward=False)); mid += 1
            continue
        fwd = kind < 0.12
        n = 3 if rnd.random() < 0.2 else 1                           # альбом из 3 картинок
        gid = 10_000_000 + mid if n > 1 else None
        for j in range(n):
            msgs.append(dict(msg_id=mid, date=t.isoformat(), text="Новый арт #fennec #military" if (j == 0 and rnd.random() < 0.5) else ("Арт #desert" if j == 0 else ""),
                             grouped_id=gid, media_type="photo", views=views, forwards=int(views * 0.01),
                             replies=rnd.randint(0, 5), reactions=int(views * 0.04 * rnd.uniform(0.7, 1.3)) if j == 0 else 0,
                             is_forward=fwd, fwd_chat=("-1002222" if fwd and chat_id == "-1001111" and k % 2 else None)))
            mid += 1
    return msgs


class FakeBackend:
    CHANNELS = {
        "-1001111": {"title": "Арт SFW", "username": "my_art_sfw", "msgs": _make_channel("-1001111", 700, 1, 800)},
        "-1002222": {"title": "Арт 18+", "username": None, "msgs": _make_channel("-1002222", 400, 2, 1500)},
    }

    BLOCKED = {"full"}   # имитация DPI: обычный протокол режется, обфусцированный и прокси проходят

    def __init__(self, session_path: Path, api_id, api_hash, net=None):
        self.session = Path(str(session_path) + ".session")
        self.phone = None
        self.net = net or {}

    async def ping(self):
        import asyncio
        if not self.net.get("proxy") and self.net.get("transport", "obfuscated") in self.BLOCKED:
            await asyncio.sleep(0.3)
            raise TimeoutError()

    async def close(self):
        pass

    async def is_authorized(self):
        return self.session.exists()

    async def send_code(self, phone):
        if len(phone) < 8:
            raise AuthError("Неверный номер телефона")
        self.phone = phone

    async def sign_in(self, code):
        if code != "12345":
            raise AuthError("Неверный код")
        raise NeedPassword()

    async def sign_in_password(self, pw):
        if pw != "pw":
            raise AuthError("Неверный облачный пароль")
        self.session.write_text("ok")

    async def me(self):
        return {"name": "Тест Тестов", "username": "tester"}

    async def log_out(self):
        self.session.unlink(missing_ok=True)

    async def forum_topics(self, chat_id):
        return [{"id": 1, "title": "General"}, {"id": 5, "title": "SFW-арт"}, {"id": 7, "title": "18+ арт"}, {"id": 9, "title": "Русский"}]

    async def channels(self):
        return [{"chat_id": k, "title": v["title"], "username": v["username"], "broadcast": True, "members": 1000}
                for k, v in self.CHANNELS.items()] + [{"chat_id": "-1003333", "title": "Арт-группа", "username": None,
                                                        "broadcast": False, "members": 300}]

    async def latest_id(self, chat_id):
        return self.CHANNELS[chat_id]["msgs"][-1]["msg_id"]

    async def messages(self, chat_id, min_id, limit, previews_dir, username=None):
        out = [dict(m) for m in self.CHANNELS[chat_id]["msgs"] if m["msg_id"] > min_id][:limit]
        if previews_dir:
            for m in out:
                if m["media_type"] == "photo" and (m["grouped_id"] is None or m["msg_id"] == m["grouped_id"] - 10_000_000):
                    p = previews_dir / f"tg_{chat_id}_{m['msg_id']}.jpg"
                    if not p.exists():
                        Image.new("RGB", (64, 80), ((m["msg_id"] * 37) % 255, 90, 140)).save(p)
                    m["preview"] = p.name
        return out

    async def refresh(self, chat_id, ids, previews_dir=None):
        ids = set(ids)
        return [dict(m, views=int(m["views"] * 1.1)) for m in self.CHANNELS[chat_id]["msgs"] if m["msg_id"] in ids]
