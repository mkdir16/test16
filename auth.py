import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

# initData старше этого считается просроченным (защита от replay)
MAX_AUTH_AGE_SECONDS = 24 * 3600


def verify_telegram_init_data(init_data: str) -> dict | None:
    """
    Проверяет подпись initData от Telegram.
    Возвращает dict с данными юзера или None если подпись неверна.
    """
    try:
        if not init_data:
            return None
        if not BOT_TOKEN:
            # Токен не настроен — авторизация невозможна, но не падаем с 500
            print("WARNING: BOT_TOKEN is not set, Telegram check unavailable")
            return None

        # Парсим строку вида "hash=abc&user=%7B...%7D&auth_date=..."
        # parse_qs уже URL-декодирует значения, поэтому unquote повторно не нужен
        parsed = parse_qs(init_data, keep_blank_values=True)

        # Извлекаем hash (подпись)
        received_hash = parsed.get("hash", [None])[0]
        if not received_hash:
            return None

        # Убираем hash из строки для проверки
        data_check_parts = []
        for key, values in sorted(parsed.items()):
            if key != "hash":
                data_check_parts.append(f"{key}={values[0]}")

        data_check_string = "\n".join(data_check_parts)

        # Считаем HMAC-SHA256
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        # Сравниваем подписи
        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        # Проверка свежести (защита от replay-атак)
        auth_date_raw = parsed.get("auth_date", [None])[0]
        if auth_date_raw:
            try:
                auth_date = int(auth_date_raw)
                if time.time() - auth_date > MAX_AUTH_AGE_SECONDS:
                    return None
            except (ValueError, TypeError):
                return None

        # Достаём данные юзера (parse_qs уже декодировал, json.loads напрямую)
        user_raw = parsed.get("user", ["{}"])[0]
        try:
            user_data = json.loads(user_raw)
        except json.JSONDecodeError:
            # Fallback для двойного кодирования
            from urllib.parse import unquote
            user_data = json.loads(unquote(user_raw))

        if not isinstance(user_data, dict) or "id" not in user_data:
            return None

        return user_data  # {"id": 123456, "first_name": "Азиз", ...}

    except Exception:
        return None
