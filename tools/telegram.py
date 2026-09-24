"""Sends approval notifications to the organisation's Telegram. Silent if not configured."""
import logging

import httpx
from django.conf import settings

log = logging.getLogger("seyalini")


def _creds(tenant):
    from core.secrets import get_secret

    return get_secret(tenant, "TELEGRAM_BOT_TOKEN"), get_secret(tenant, "TELEGRAM_CHAT_ID")


def send(text: str, tenant=None) -> bool:
    token, chat = _creds(tenant)
    if not (token and chat):
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=10)
        r.raise_for_status()
        return True
    except Exception as exc:  # a notification must never break the agent
        log.warning("Telegram send failed: %s", exc)
        return False


def notify_video(task, path) -> bool:
    """Sends the finished video itself (Telegram bots can send files up to 50 MB)."""
    token, chat = _creds(task.tenant)
    if not (token and chat):
        return False
    caption = f"Video to review - {task.product.name}\n{task.result.get('title')}\nApprove or redo: {settings.DASHBOARD_URL}/#task-{task.id}"
    try:
        with open(path, "rb") as fh:
            r = httpx.post(f"https://api.telegram.org/bot{token}/sendVideo",
                           data={"chat_id": chat, "caption": caption[:1000], "supports_streaming": "true"},
                           files={"video": ("short.mp4", fh, "video/mp4")}, timeout=120)
        r.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Telegram video send failed: %s", exc)
        return send(caption, task.tenant)


def notify_script(task) -> bool:
    s = task.result
    scenes = "\n".join(f"  {i}. {sc.get('on_screen_text', '')}" for i, sc in enumerate(s.get("scenes", []), 1))
    text = (f"New Short to review - {task.product.name}\n\n"
            f"{s.get('title')}\nHook: {s.get('hook')}\n{scenes}\n\n"
            f"Approve or redo: {settings.DASHBOARD_URL}/#task-{task.id}")
    return send(text, task.tenant)
