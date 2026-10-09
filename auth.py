"""
Авторизация сайта (без Telegram).

Ученики: без пароля. Браузер придумывает себе UUID (localStorage),
шлёт его в X-User-Id + имя в X-User-Name. Сервер создаёт/находит юзера.
Админ: пароль из ADMIN_PASSWORD, вход через POST /admin/login,
дальше токен в X-Admin-Token (HMAC-подпись, живёт 30 дней).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dotenv import load_dotenv

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "") or ADMIN_PASSWORD or "dev-secret-change-me"

TOKEN_TTL_SECONDS = 30 * 24 * 3600


def admin_configured() -> bool:
    return bool(ADMIN_PASSWORD)


def verify_admin_password(password: str) -> bool:
    if not ADMIN_PASSWORD or not password:
        return False
    return hmac.compare_digest(password, ADMIN_PASSWORD)


def create_admin_token() -> str:
    payload = {"exp": int(time.time()) + TOKEN_TTL_SECONDS,
               "r": secrets.token_hex(8)}
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()).decode()
    sig = hmac.new(SECRET_KEY.encode(), body.encode(),
                   hashlib.sha256).hexdigest()
    return body + "." + sig


def verify_admin_token(token: str) -> bool:
    try:
        if not token or "." not in token:
            return False
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), body.encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        payload = json.loads(base64.urlsafe_b64decode(body.encode()).decode())
        return int(payload.get("exp", 0)) > time.time()
    except Exception:
        return False
