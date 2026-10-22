#!/usr/bin/env python3
"""Одноразовая авторизация для площадок, которым нужен вход через браузер.

  python cli/auth.py deviantart
  python cli/auth.py vk
  python cli/auth.py vk --manual   # если локальный адрес не подходит, вставить ссылку руками
"""
import argparse
import base64
import hashlib
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.common import load_config, save_token  # noqa: E402
from core.oauth import get_auth_code  # noqa: E402


def auth_deviantart(c: dict, manual: bool):
    state = secrets.token_urlsafe(16)
    url = "https://www.deviantart.com/oauth2/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": c["client_id"],
        "redirect_uri": c["redirect_uri"],
        "scope": "stash publish",
        "state": state,
    })
    res = get_auth_code(url, c["redirect_uri"], state, manual)
    r = requests.post("https://www.deviantart.com/oauth2/token", data={
        "grant_type": "authorization_code",
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "code": res["code"],
        "redirect_uri": c["redirect_uri"],
    }, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise SystemExit(f"DeviantArt не выдал токен: {j}")
    save_token("deviantart", {
        "access_token": j["access_token"],
        "refresh_token": j["refresh_token"],
        "expires_at": time.time() + int(j.get("expires_in", 3600)),
    })
    print("DeviantArt подключён.")


def auth_vk(c: dict, manual: bool):
    verifier = secrets.token_urlsafe(64)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://id.vk.com/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": c["app_id"],
        "redirect_uri": c["redirect_uri"],
        "scope": "wall photos groups",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    res = get_auth_code(url, c["redirect_uri"], state, manual)
    if "device_id" not in res:
        raise SystemExit("VK не прислал device_id — проверьте, что приложение создано в VK ID как веб-приложение.")
    r = requests.post("https://id.vk.com/oauth2/auth", data={
        "grant_type": "authorization_code",
        "code_verifier": verifier,
        "redirect_uri": c["redirect_uri"],
        "code": res["code"],
        "client_id": c["app_id"],
        "device_id": res["device_id"],
        "state": state,
    }, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise SystemExit(f"VK не выдал токен: {j}")
    save_token("vk", {
        "access_token": j["access_token"],
        "refresh_token": j["refresh_token"],
        "device_id": res["device_id"],
        "expires_at": time.time() + int(j.get("expires_in", 3600)),
    })
    print("VK подключён.")


def main():
    ap = argparse.ArgumentParser(description="Авторизация в DeviantArt или VK.")
    ap.add_argument("platform", choices=["deviantart", "vk"])
    ap.add_argument("--manual", action="store_true", help="вставить адрес из браузера вручную")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.platform not in cfg:
        raise SystemExit(f"В config.toml нет раздела [{args.platform}]")
    {"deviantart": auth_deviantart, "vk": auth_vk}[args.platform](cfg[args.platform], args.manual)


if __name__ == "__main__":
    main()
