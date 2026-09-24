"""Keeps tenants/<org>/products/<app>/ in sync with what you approve in the
dashboard, so the files in Git stay the single source of truth."""
from pathlib import Path

import yaml
from django.conf import settings

SKIP_KEYS = {"draft"}  # work-in-progress stays in the database only


def product_dir(product) -> Path:
    return Path(settings.TENANTS_DIR) / product.tenant.slug / "products" / product.slug


def write_product_files(product) -> Path:
    folder = product_dir(product)
    (folder / "assets").mkdir(parents=True, exist_ok=True)
    (folder / "brief.md").write_text(product.brief or "", encoding="utf-8")
    data = {"slug": product.slug, "name": product.name, "status": product.status,
            "config": {k: v for k, v in (product.config or {}).items() if k not in SKIP_KEYS}}
    (folder / "product.yaml").write_text(
        "# Written by Seyalini when you activated the brief. Safe to edit; run load_tenants after.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return folder
