"""The brand strategist: turns your answers + the Play Store listing + the app's
screenshots into a strong brand brief that every agent reads.

It never invents facts. Anything unclear comes back as a question for you.
Flow: research_product() -> draft for review -> you edit -> activate()
"""
import base64
import json
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from agents.llm import parse_json
from agents.runtime import AgentRuntime
from core.models import Approval, Event, Product, Task
from core.product_files import product_dir, write_product_files
from tools import playstore, telegram

DEFAULT_PILLARS = [
    {"key": "watch_the_magic", "name": "See it in action", "idea": "The best moment of the app, shown in real use"},
    {"key": "problem_solution", "name": "Problem → solution", "idea": "A real everyday problem the app solves"},
    {"key": "quick_tip", "name": "Quick tip", "idea": "A useful tip for the audience, linked to the app"},
    {"key": "fun_fact", "name": "Fun fact", "idea": "A surprising fact related to the app's topic"},
    {"key": "feature_spotlight", "name": "Feature spotlight", "idea": "One feature, explained in 20 seconds"},
]

SYSTEM = """You are a senior brand strategist for mobile apps. You write the brand brief that an AI
marketing team (script writer, video maker, publisher) will follow for every short video.

Use ONLY facts from the founder's answers, the store listing and the screenshots. Never invent
numbers, awards, reviews or features. If something important is missing or unclear, add it to
"questions" instead of guessing. Make it sharper than the founder's notes: clear benefits,
the emotional reason people install, concrete visual moments from the screenshots, and
content pillars that can produce 60+ different videos a month without feeling repetitive.
If the app is for children, respect platform kids rules (YouTube "Made for Kids", no data
collection claims beyond what is stated, no real children's faces in AI visuals).

Reply with ONLY a JSON object:
{
 "brief_markdown": "the full brief in Markdown with these sections: # <App> - Brand Brief; ## 1. The App (one-line pitch, what happens in the app, key features, pricing, store link); ## 2. Audience (primary, secondary, language); ## 3. Why people install (benefits, pains solved, proof points from the listing); ## 4. Tone & Look; ## 5. Content Pillars (table: pillar, idea, example hook); ## 6. Every Short must have; ## 7. Posting rules; ## 8. Never do; ## 9. Hashtags",
 "pillars": [{"key": "snake_case", "name": "short name", "idea": "one line", "uses_letter": false}],
 "visual_style": "one paragraph describing the look for AI image/video prompts (colours, style, characters, setting, lighting) based on the screenshots",
 "hashtags": ["#..."],
 "made_for_kids": true,
 "improvements": ["what you added or sharpened compared to the founder's notes"],
 "questions": ["short questions for the founder about gaps"]
}
Give 5-7 pillars. Set uses_letter=true only for pillars that rotate through the alphabet A-Z."""


def _answers_text(a: dict) -> str:
    labels = [("name", "App name"), ("store_url", "Store link"), ("what_it_does", "What it does"),
              ("audience", "Who it's for"), ("pricing", "Pricing"), ("languages", "Video languages"),
              ("goal", "Main goal"), ("tone", "Tone"), ("brand_colour", "Brand colour"), ("extra", "Extra notes")]
    return "\n".join(f"- {label}: {a[k]}" for k, label in labels if a.get(k))


def _listing_text(listing: dict) -> str:
    if not listing:
        return "(store listing not available)"
    keys = ["title", "developer", "category", "content_rating", "rating", "price", "in_app_purchases", "contains_ads", "updated"]
    lines = [f"- {k}: {listing[k]}" for k in keys if listing.get(k) not in (None, "")]
    return "\n".join(lines) + f"\n\nDescription:\n{(listing.get('description') or '')[:5000]}"


def _image_parts(paths: list[Path], limit=4) -> list[dict]:
    parts = []
    for p in paths[:limit]:
        try:
            from PIL import Image
            import io
            im = Image.open(p).convert("RGB")
            im.thumbnail((768, 768))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode()
            parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        except Exception:
            continue
    return parts


def _draft_without_ai(product: Product, a: dict, listing: dict) -> dict:
    """Dry run: assembles a clean brief from your answers + the listing (no AI, free)."""
    name = a.get("name") or product.name
    desc = listing.get("description") or ""
    pillars = (product.config or {}).get("pillars") or DEFAULT_PILLARS
    rows = "\n".join(f"| {p['name']} | {p['idea']} | |" for p in pillars)
    missing = [q for k, q in [("what_it_does", "What exactly happens in the app, step by step?"),
                              ("audience", "Who is it for (age / role)?"), ("pricing", "Is it free, freemium or paid?"),
                              ("languages", "Which languages should videos use?")] if not a.get(k)]
    brief = f"""# {name} - Brand Brief
_Draft assembled without AI (dry run). Add GEMINI_API_KEY and click "Improve with AI" for a sharper version._

## 1. The App
- **Name:** {name}
- **Store link:** {a.get('store_url') or listing.get('url') or '[add link]'}
- **What happens in the app:** {a.get('what_it_does') or '[describe]'}
- **Pricing:** {a.get('pricing') or listing.get('price') or '[free / freemium / paid]'}
- **Category / rating:** {listing.get('category', '')} {listing.get('content_rating', '')} {listing.get('rating', '')}

## 2. Audience
- {a.get('audience') or '[who is it for]'}
- **Video language(s):** {a.get('languages') or 'English'}

## 3. From the store listing
{desc[:2500] or '(listing not available)'}

## 4. Tone & Look
- {a.get('tone') or 'Friendly, clear, positive'}
- Brand colour: {a.get('brand_colour') or '[colour]'}

## 5. Content Pillars
| Pillar | Idea | Example hook |
|---|---|---|
{rows}

## 6. Every Short must have
- Hook in the first 2 seconds, captions on screen, logo, end card with store call to action.

## 7. Notes from the founder
{a.get('extra') or '-'}
"""
    return {"brief_markdown": brief, "pillars": pillars, "visual_style": "", "hashtags": [],
            "made_for_kids": bool(re.search(r"kid|child|preschool|toddler", f"{a} {desc}", re.I)),
            "improvements": ["Dry run: assembled from your answers and the store listing without AI."],
            "questions": missing}


def research_product(product: Product, answers: dict, parent: Task | None = None) -> Task:
    agent = AgentRuntime(product.tenant, "marketing")
    task = Task.objects.create(tenant=product.tenant, product=product, agent_key="marketing", kind="product_brief",
                               title=f"Brand brief: {product.name}", status="running", payload={"answers": answers},
                               parent=parent)
    agent.log("task_started", f"Researching {product.name} for its brand brief", task=task)
    try:
        listing, shots, store_error = {}, [], ""
        if answers.get("store_url"):
            try:
                listing = playstore.fetch(answers["store_url"])
                agent.log("store_read", f"Read Play Store listing: {listing.get('title')} "
                                        f"({len(listing.get('screenshots', []))} screenshots)", task=task)
                shots = playstore.download_screenshots(listing.get("screenshots", []), product_dir(product) / "assets" / "screens")
            except Exception as exc:
                store_error = str(exc)[:150]
                agent.log("store_unreachable", f"Could not read the store listing: {store_error}", task=task)

        if agent.dry_run:
            draft = _draft_without_ai(product, answers, listing)
            agent.log("llm_call", "Brief assembled without AI (dry run)", task=task, dry_run=True)
        else:
            current = f"\n\nCurrent brief (improve it, keep what is correct):\n{product.brief}" if product.brief else ""
            content = [{"type": "text", "text": f"# Founder's answers\n{_answers_text(answers)}\n\n"
                                                f"# Play Store listing\n{_listing_text(listing)}{current}\n\n"
                                                f"The images are the app's store screenshots."}]
            content += _image_parts(shots)
            raw = agent.think([{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                              task=task, json_mode=True)
            draft = parse_json(raw)
            if not draft.get("brief_markdown"):
                raise ValueError("The AI returned no brief")
        draft["pillars"] = _clean_pillars(draft.get("pillars")) or DEFAULT_PILLARS
        if store_error:
            draft.setdefault("questions", []).insert(0, f"I couldn't open the Play Store page ({store_error}). "
                                                        "Paste the store description into 'Anything else' and run Improve with AI again.")
        draft["listing"] = {k: v for k, v in listing.items() if k != "description"} | {"has_description": bool(listing.get("description"))}
        draft["screens_saved"] = len(shots)
    except Exception as exc:
        task.status = "failed"
        task.result = {"error": str(exc)[:500]}
        task.save()
        agent.log("task_failed", f"Brief failed: {str(exc)[:200]}", task=task)
        raise

    product.config = {**(product.config or {}), "draft": draft, "onboarding": answers}
    if product.status != "live":
        product.status = "review"
    product.save()
    task.result = {"title": f"Brand brief for {product.name}", "questions": draft.get("questions", []),
                   "improvements": draft.get("improvements", [])}
    task.status = "awaiting_approval"
    task.save()
    Approval.objects.create(tenant=product.tenant, task=task)
    agent.log("awaiting_approval", f"Brand brief ready for your review: {product.name}", task=task)
    telegram.send(f"Brand brief ready for review: {product.name}\n{settings.DASHBOARD_URL}/products/{product.slug}/", tenant=product.tenant)
    return task


def _clean_pillars(pillars) -> list[dict]:
    out, seen = [], set()
    for p in pillars or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        key = slugify(p.get("key") or p["name"]).replace("-", "_")[:40] or f"pillar_{len(out) + 1}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "name": str(p["name"])[:60], "idea": str(p.get("idea", ""))[:200],
                    "uses_letter": bool(p.get("uses_letter"))})
    return out[:8]


def parse_pillars_text(text: str) -> list[dict]:
    """Editable format on the review page: one pillar per line, 'Name | idea' (add ' | letters' to rotate A-Z)."""
    pillars = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if not parts or not parts[0]:
            continue
        pillars.append({"key": parts[0], "name": parts[0], "idea": parts[1] if len(parts) > 1 else "",
                        "uses_letter": len(parts) > 2 and parts[2].lower().startswith("letter")})
    return _clean_pillars(pillars)


def pillars_to_text(pillars: list[dict]) -> str:
    return "\n".join(f"{p['name']} | {p.get('idea', '')}" + (" | letters" if p.get("uses_letter") else "") for p in pillars)


@transaction.atomic
def activate(product: Product, user, brief: str, pillars_text: str, visual_style: str) -> Product:
    cfg = dict(product.config or {})
    draft = cfg.pop("draft", {}) or {}
    pillars = parse_pillars_text(pillars_text) or draft.get("pillars") or cfg.get("pillars") or DEFAULT_PILLARS
    cfg.update({
        "marketing": True,
        "pillars": pillars,
        "visual_style": visual_style.strip(),
        "store_url": (cfg.get("onboarding") or {}).get("store_url") or cfg.get("store_url", ""),
        "platforms": cfg.get("platforms") or ["youtube_shorts", "instagram_reels", "facebook_reels"],
        "shorts_per_day": cfg.get("shorts_per_day", 2),
        "topic_repeat_days": cfg.get("topic_repeat_days", 7),
    })
    if draft.get("made_for_kids") is not None:
        cfg["youtube_made_for_kids"] = bool(draft["made_for_kids"]) or cfg.get("youtube_made_for_kids", False)
    if draft.get("hashtags"):
        cfg["hashtags"] = draft["hashtags"]
    product.brief = brief.strip()
    product.config = cfg
    product.status = "live"
    product.save()
    write_product_files(product)

    for t in Task.objects.filter(product=product, kind="product_brief", status="awaiting_approval"):
        t.status = "approved"
        t.save()
        Approval.objects.filter(task=t, decision="pending").update(decision="approved", decided_by=user, decided_at=timezone.now())
    Event.objects.create(tenant=product.tenant, agent_key="marketing", kind="brief_activated",
                         message=f"You activated the brand brief for {product.name}. Marketing now uses it.")
    return product
