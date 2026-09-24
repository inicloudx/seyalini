"""Loads tenants from files in tenants/<slug>/ into the database.

Files are the source of truth (they live in Git, so every laptop has them).
Agent cards are versioned: if a YAML changes, a NEW version is created and the
old one is kept for rollback. Nothing is ever deleted.
"""
from decimal import Decimal
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import AgentCard, Membership, Product, Tenant

CARD_FIELDS = ("name", "role", "status", "autonomy", "model", "monthly_budget_usd", "config")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _card_values(data: dict) -> dict:
    return {
        "name": data.get("name", data["key"].title()),
        "role": data.get("role", ""),
        "status": data.get("status", "planned"),
        "autonomy": int(data.get("autonomy", 1)),
        "model": data.get("model", ""),
        "monthly_budget_usd": Decimal(str(data.get("monthly_budget_usd", "5"))),
        "config": data.get("config", {}) or {},
    }


def sync_agent(tenant: Tenant, data: dict) -> tuple[AgentCard, str]:
    values = _card_values(data)
    current = AgentCard.objects.filter(tenant=tenant, key=data["key"], is_current=True).first()
    if current is None:
        return AgentCard.objects.create(tenant=tenant, key=data["key"], version=1, changelog=data.get("changelog", "First version"), **values), "created"
    if all(getattr(current, f) == values[f] for f in CARD_FIELDS):
        return current, "unchanged"
    current.is_current = False
    current.save(update_fields=["is_current"])
    card = AgentCard.objects.create(
        tenant=tenant, key=data["key"], version=current.version + 1,
        changelog=data.get("changelog", "Updated from file"), **values,
    )
    return card, f"v{current.version} -> v{card.version}"


@transaction.atomic
def load_tenant(folder: Path) -> list[str]:
    log = []
    meta = _read_yaml(folder / "tenant.yaml")
    tenant, _ = Tenant.objects.update_or_create(
        slug=meta.get("slug", folder.name),
        defaults={"name": meta.get("name", folder.name), "theme": meta.get("theme", {}), "settings": meta.get("settings", {})},
    )
    log.append(f"tenant {tenant.slug}")

    for pdir in sorted((folder / "products").glob("*/")):
        if not (pdir / "product.yaml").exists():
            continue  # an app still being set up in the dashboard (only assets so far): it lives in the database
        pmeta = _read_yaml(pdir / "product.yaml")
        brief_file = pdir / "brief.md"
        slug = pmeta.get("slug", pdir.name)
        existing = Product.objects.filter(tenant=tenant, slug=slug).first()
        config = pmeta.get("config", {}) or {}
        if existing and (existing.config or {}).get("draft"):
            config["draft"] = existing.config["draft"]  # keep a brief that is waiting for your review
        Product.objects.update_or_create(
            tenant=tenant, slug=slug,
            defaults={
                "name": pmeta.get("name", pdir.name),
                "status": pmeta.get("status", "live"),
                "brief": brief_file.read_text(encoding="utf-8") if brief_file.exists() else "",
                "config": config,
            },
        )
        log.append(f"  product {pdir.name}")

    for afile in sorted((folder / "agents").glob("*.yaml")):
        data = _read_yaml(afile)
        data.setdefault("key", afile.stem)
        card, change = sync_agent(tenant, data)
        log.append(f"  agent {card.key}: {change}")

    # superusers can always see every tenant; make them owners for convenience
    for user in get_user_model().objects.filter(is_superuser=True):
        Membership.objects.get_or_create(user=user, tenant=tenant, defaults={"role": "owner"})
    return log


def load_all(base: Path | None = None) -> list[str]:
    base = base or settings.TENANTS_DIR
    log = []
    for folder in sorted(p for p in base.iterdir() if (p / "tenant.yaml").exists()):
        log += load_tenant(folder)
    return log
