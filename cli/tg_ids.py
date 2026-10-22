#!/usr/bin/env python3
"""Помощник: узнать id каналов, групп и номера тем для config.toml.

1. Запустите: python cli/tg_ids.py
2. Напишите что-нибудь в нужной теме группы (или опубликуйте пост в канале), где бот — админ.
3. Скрипт покажет id чата и номер темы. Остановить — Ctrl+C.
"""
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.common import load_config  # noqa: E402


def main():
    token = load_config().get("telegram", {}).get("bot_token")
    if not token:
        raise SystemExit("В config.toml не указан [telegram] bot_token")
    api = f"https://api.telegram.org/bot{token}/"
    print("Жду сообщения… Напишите в нужную тему группы или опубликуйте пост в канале. Ctrl+C — выход.\n")
    offset = None
    seen = set()
    try:
        while True:
            params = {"timeout": 30, "allowed_updates": '["message","channel_post"]'}
            if offset is not None:
                params["offset"] = offset
            j = requests.get(api + "getUpdates", params=params, timeout=40).json()
            if not j.get("ok"):
                raise SystemExit(f"Ошибка Telegram: {j.get('description')}")
            for u in j["result"]:
                offset = u["update_id"] + 1
                m = u.get("message") or u.get("channel_post") or {}
                chat = m.get("chat") or {}
                topic = m.get("message_thread_id") if m.get("is_topic_message") else None
                key = (chat.get("id"), topic)
                if key in seen or not chat:
                    continue
                seen.add(key)
                kind = {"channel": "канал", "supergroup": "группа", "group": "группа"}.get(chat.get("type"), chat.get("type"))
                line = f"{kind} «{chat.get('title')}»: id = {chat.get('id')}"
                if topic:
                    line += f", тема № {topic}"
                    topic_name = (m.get("reply_to_message") or {}).get("forum_topic_created", {}).get("name")
                    if topic_name:
                        line += f" («{topic_name}»)"
                print(line)
    except KeyboardInterrupt:
        print("\nГотово.")


if __name__ == "__main__":
    main()
