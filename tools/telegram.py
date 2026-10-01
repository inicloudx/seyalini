"""Telegram: the Manager's line to your phone. Messages, videos and Yes / No buttons; silent if not connected."""
import json
import logging

import httpx
from django.conf import settings

log = logging.getLogger("seyalini")

API = "https://api.telegram.org/bot{token}/{method}"


def _creds(tenant):
    from core.secrets import get_secret

    return get_secret(tenant, "TELEGRAM_BOT_TOKEN"), get_secret(tenant, "TELEGRAM_CHAT_ID")


def call(token: str, method: str, payload: dict | None = None, timeout=15) -> dict:
    r = httpx.post(API.format(token=token, method=method), json=payload or {}, timeout=timeout)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram {method} failed")
    return data.get("result")


def keyboard(buttons: list) -> dict | None:
    """[[label, action], ...] -> Telegram inline buttons, two per row."""
    if not buttons:
        return None
    keys = [{"text": label[:40], "callback_data": action[:64]} for label, action in buttons]
    return {"inline_keyboard": [keys[i:i + 2] for i in range(0, len(keys), 2)]}


def send(text: str, tenant=None) -> bool:
    return send_chat(tenant, text)


def send_chat(tenant, text: str, buttons: list | None = None, chat_id: str | None = None) -> bool:
    """A chat message with optional Yes / No buttons. Silent if Telegram is not connected."""
    token, chat = _creds(tenant)
    chat = chat_id or chat
    if not (token and chat):
        return False
    payload = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    if kb := keyboard(buttons or []):
        payload["reply_markup"] = kb
    try:
        call(token, "sendMessage", payload)
        return True
    except Exception as exc:  # a chat message must never break the agent
        log.warning("Telegram chat send failed: %s", exc)
        return False


def send_video(tenant, path, caption: str, buttons: list | None = None) -> bool:
    """The finished video itself, with buttons under it (bots can send files up to 50 MB)."""
    token, chat = _creds(tenant)
    if not (token and chat):
        return False
    data = {"chat_id": chat, "caption": caption[:1000], "supports_streaming": "true"}
    if kb := keyboard(buttons or []):
        data["reply_markup"] = json.dumps(kb)
    try:
        with open(path, "rb") as fh:
            r = httpx.post(API.format(token=token, method="sendVideo"), data=data,
                           files={"video": ("short.mp4", fh, "video/mp4")}, timeout=120)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Telegram video send failed: %s", exc)
        return False


def webhook_secret(tenant) -> str:
    import hashlib
    import hmac

    return hmac.new(settings.SECRET_KEY.encode(), f"telegram:{tenant.slug}".encode(), hashlib.sha256).hexdigest()[:48]
