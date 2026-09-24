"""Encrypts each organisation's API keys and decides which key an agent uses.

Rules:
- An organisation's own key always wins.
- The platform key from .env (yours) is only used by organisations marked
  `use_platform_keys: true` in their settings (INIXR). Everyone else must bring
  their own key; without one they run in free dry-run mode.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

KNOWN = {
    "GEMINI_API_KEY": "Gemini API key (scripts, briefs, images, Veo video)",
    "OPENAI_API_KEY": "OpenAI API key (optional)",
    "ANTHROPIC_API_KEY": "Anthropic API key (optional)",
    "TELEGRAM_BOT_TOKEN": "Telegram bot token (optional, notifications)",
    "TELEGRAM_CHAT_ID": "Telegram chat id (optional)",
}


def _fernet() -> Fernet:
    raw = os.environ.get("SEYALINI_ENCRYPTION_KEY") or settings.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def _hint(value: str) -> str:
    return f"{value[:4]}…{value[-4:]}" if len(value) > 10 else "••••"


def set_secret(tenant, name: str, value: str):
    from .models import TenantSecret

    value = (value or "").strip()
    if not value:
        TenantSecret.objects.filter(tenant=tenant, name=name).delete()
        return None
    obj, _ = TenantSecret.objects.update_or_create(
        tenant=tenant, name=name,
        defaults={"value_encrypted": _fernet().encrypt(value.encode()).decode(), "hint": _hint(value)})
    return obj


def own_secret(tenant, name: str) -> str:
    from .models import TenantSecret

    obj = TenantSecret.objects.filter(tenant=tenant, name=name).first() if tenant else None
    if not obj:
        return ""
    try:
        return _fernet().decrypt(obj.value_encrypted.encode()).decode()
    except InvalidToken:  # encryption key changed: treat as missing, owner re-enters it
        return ""


def uses_platform_keys(tenant) -> bool:
    return bool(tenant and (tenant.settings or {}).get("use_platform_keys"))


def get_secret(tenant, name: str) -> str:
    return own_secret(tenant, name) or (os.environ.get(name, "") if uses_platform_keys(tenant) else "")


def key_for_model(tenant, model: str) -> str:
    provider = (model or "gemini/").split("/")[0].lower()
    name = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(provider, "GEMINI_API_KEY")
    return get_secret(tenant, name)


def is_dry_run(tenant) -> bool:
    """Free sample mode: forced by LLM_DRY_RUN=1, or this organisation has no Gemini key."""
    return bool(settings.LLM_DRY_RUN) or not get_secret(tenant, "GEMINI_API_KEY")
