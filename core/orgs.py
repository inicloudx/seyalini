"""Creating a new organisation (tenant) with its owner and default AI team."""
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.text import slugify

from .loader import sync_agent
from .models import Membership, Tenant

TEMPLATE = Path(settings.BASE_DIR) / "tenants" / "_template" / "agents"


def unique_slug(name: str) -> str:
    base = slugify(name)[:40] or "org"
    slug, n = base, 2
    while Tenant.objects.filter(slug=slug).exists() or slug.startswith("_"):
        slug, n = f"{base}-{n}", n + 1
    return slug


@transaction.atomic
def create_organisation(name: str, owner_username: str, owner_email: str, password: str,
                        use_platform_keys: bool = False, accent: str = "#2F4BC7"):
    User = get_user_model()
    if User.objects.filter(username__iexact=owner_username).exists():
        raise ValueError(f"The username “{owner_username}” is already taken.")
    tenant = Tenant.objects.create(
        slug=unique_slug(name), name=name,
        theme={"accent": accent, "logo_text": "".join(w[0] for w in name.split()[:2]).upper() or "S"},
        settings={"use_platform_keys": use_platform_keys, "timezone": "Asia/Kolkata", "owner_name": owner_username},
    )
    for f in sorted(TEMPLATE.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        data.setdefault("key", f.stem)
        sync_agent(tenant, data)
    user = User.objects.create_user(username=owner_username, email=owner_email, password=password)
    Membership.objects.create(user=user, tenant=tenant, role="owner")
    return tenant, user


def add_member(tenant, username: str, email: str, password: str, role: str):
    User = get_user_model()
    user = User.objects.filter(username__iexact=username).first()
    created = False
    if user is None:
        user = User.objects.create_user(username=username, email=email, password=password)
        created = True
    Membership.objects.update_or_create(user=user, tenant=tenant, defaults={"role": role})
    return user, created
