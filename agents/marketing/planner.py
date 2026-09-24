"""Chooses today's topic WITHOUT calling an AI model (free).

Rotates content pillars (least recently used first) and letters A-Z, and never
repeats a letter inside the product's `topic_repeat_days` window.
"""
import string
from datetime import timedelta

from django.utils import timezone

from core.models import Product, Task

LETTERS = string.ascii_uppercase


def _recent_scripts(product: Product, days: int):
    since = timezone.now() - timedelta(days=days)
    return Task.objects.filter(product=product, kind="short_script", created__gte=since).exclude(status="failed")


def plan_next(product: Product, slot: str = "any") -> dict:
    cfg = product.config or {}
    pillars = cfg.get("pillars") or [{"key": "general", "name": "General", "idea": "Show what the app does"}]
    history = list(Task.objects.filter(product=product, kind="short_script").exclude(status="failed")
                   .order_by("-created").values_list("payload", flat=True)[:200])

    # 1. pillar used longest ago (never used wins)
    last_seen = {}
    for i, p in enumerate(history):
        last_seen.setdefault(p.get("pillar"), i)
    pillar = max(pillars, key=lambda p: last_seen.get(p["key"], 10_000))

    plan = {"pillar": pillar["key"], "pillar_name": pillar["name"], "pillar_idea": pillar.get("idea", ""), "slot": slot}

    # 2. letter: next in A-Z after the last one, skipping recently used
    if pillar.get("uses_letter"):
        repeat_days = int(cfg.get("topic_repeat_days", 7))
        recent = {t.payload.get("letter") for t in _recent_scripts(product, repeat_days)}
        last = next((p.get("letter") for p in history if p.get("letter")), None)
        start = (LETTERS.index(last) + 1) if last and len(last) == 1 and last in LETTERS else 0
        order = LETTERS[start:] + LETTERS[:start]
        plan["letter"] = next((c for c in order if c not in recent), order[0])
        word = (cfg.get("letter_words") or {}).get(plan["letter"])
        if word:
            plan["word"] = word  # the real scene in the app for this letter
    return plan
