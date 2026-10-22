"""Синтетическая история двух каналов с известными «эффектами» — для проверки прогноза и выводов.

Запуск: CROSSPOST_DATA=<папка> python -m tests.make_fake_history
Эффекты: лис ×1.6, кимоно ×1.3, дракон ×0.7, вечер пятницы ×1.35, альбом ×0.85, SFW в 18+ ×0.6.
"""
import math
import random
from datetime import datetime, timedelta, timezone

from app import db

EFFECTS = {"fox": 1.6, "kimono": 1.3, "dragon": 0.7, "wolf": 1.0, "cat": 1.05, "beach": 1.1, "night": 0.95,
           "smile": 1.0, "dress": 1.0, "forest": 0.9, "rain": 1.0, "sword": 0.85}
CURVE = {1: 0.15, 3: 0.32, 6: 0.45, 12: 0.58, 24: 0.7, 48: 0.84, 72: 0.9, 120: 0.97}


def make(n_per_channel: int = 320, seed: int = 1):
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc)
    with db.tx() as c:
        for cid, title, rating in (("-1001111", "Арт SFW", "sfw"), ("-1002222", "Арт 18+", "nsfw")):
            c.execute("INSERT OR REPLACE INTO channels (platform, chat_id, title, username, rating, selected) VALUES ('telegram',?,?,?,?,1)",
                      (cid, title, "my_art_sfw" if rating == "sfw" else None, rating))
            for i in range(n_per_channel):
                age_days = rnd.uniform(0.2, 700)
                dt = now - timedelta(days=age_days)
                local = dt.astimezone()
                base = 300 * math.exp(1.2 * (1 - age_days / 700))     # канал рос
                tags = rnd.sample(list(EFFECTS), 3)
                mult = math.prod(EFFECTS[t] for t in tags)
                if local.weekday() == 4 and local.hour >= 18:
                    mult *= 1.35
                album = rnd.random() < 0.3
                if album:
                    mult *= 0.85
                text = "Новый арт"
                if rating == "nsfw":
                    sfw = rnd.random() < 0.3
                    text = ("#sfw " if sfw else "#nsfw ") + text
                    if sfw:
                        mult *= 0.6
                final = base * mult * math.exp(rnd.gauss(0, 0.35))
                frac = 1.0
                if age_days < 7:
                    h = age_days * 24
                    pts = sorted(CURVE.items())
                    frac = next((f for a, f in pts if h <= a), 1.0)
                views = int(final * frac)
                msg_id = 1000 + i * 3
                cur = c.execute("""INSERT INTO posts (platform, chat_id, msg_id, date_utc, text, media_type, n_media, views,
                                   reactions, forwards, link, updated_at) VALUES ('telegram',?,?,?,?, 'photo', ?, ?, ?, 0, ?, ?)""",
                                (cid, msg_id, dt.isoformat(timespec="seconds"), text, 3 if album else 1, views,
                                 int(views * 0.03), f"https://t.me/c/{cid[4:]}/{msg_id}", now.isoformat(timespec="seconds")))
                pid = cur.lastrowid
                c.execute("INSERT INTO messages (platform, chat_id, msg_id, post_id, media_type, views, reactions) VALUES ('telegram',?,?,?,'photo',?,?)",
                          (cid, msg_id, pid, views, int(views * 0.03)))
                for t in tags:
                    c.execute("INSERT INTO post_tags (post_id, tag, prob) VALUES (?,?,?)", (pid, t, round(rnd.uniform(0.55, 0.99), 2)))
                c.execute("INSERT INTO post_tagged (post_id, at) VALUES (?,?)", (pid, now.isoformat()))
                # замеры в первые дни (как будто следили за свежими постами)
                if 7 <= age_days <= 60:
                    for a, f in CURVE.items():
                        c.execute("INSERT INTO snapshots (post_id, taken_at, views) VALUES (?,?,?)",
                                  (pid, (dt + timedelta(hours=a)).isoformat(timespec="seconds"), int(final * f * rnd.uniform(0.93, 1.07))))


if __name__ == "__main__":
    make()
    print("ok")
