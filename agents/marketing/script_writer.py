"""Writes one YouTube Short / Reel script and puts it in your approval inbox.

Why scripts first: approving a cheap script BEFORE paying for video generation
(Step 3) means you never pay for a video you would reject.
"""
import json

from django.utils import timezone

from agents.runtime import AgentRuntime
from core.models import Approval, Product, Task
from tools import telegram

from .planner import plan_next

REQUIRED = ("title", "hook", "scenes", "caption", "hashtags")

SYSTEM = """You are the Marketing agent of {tenant}. You write short-form vertical videos
(YouTube Shorts, Instagram Reels, Facebook Reels) that make parents install the app.

Hard rules:
- 9:16 vertical, {min_s}-{max_s} seconds total, exactly {scenes} scenes of about 8 seconds each.
- Hook must grab attention in the first 2 seconds.
- Content is for families with young children: bright, safe, kind. No scary imagery,
  no real children's faces, no characters or logos owned by other companies.
- Every scene has a `veo_prompt`: a vivid, self-contained video-generation prompt
  (subject, action, setting, camera, lighting, style). No text inside the video; text goes in `on_screen_text`.
- End with a call to action to download the app.
- Follow the brand brief and EVERY learned rule below.

Reply with ONLY a JSON object:
{{"title": str, "hook": str,
  "scenes": [{{"seconds": int, "veo_prompt": str, "on_screen_text": str, "voiceover": str}}],
  "end_card": str, "caption": str, "hashtags": [str], "music_mood": str}}"""


def _user_prompt(product: Product, plan: dict, rules: list[str], avoid: list[str], redo: dict | None) -> str:
    parts = [f"# Brand brief\n{product.brief}",
             f"# Today's plan\nPillar: {plan['pillar_name']} - {plan['pillar_idea']}\nSlot: {plan['slot']}"]
    if plan.get("letter"):
        if plan.get("word"):
            parts.append(f"Letter: {plan['letter']} for {plan['word']}. This is the real 3D scene in the app for this letter: "
                         f"build the video around '{plan['letter']} for {plan['word']}' and do not use a different word.")
        else:
            parts.append(f"Letter: {plan['letter']} (build the video around this letter and a word starting with it)")
    parts.append("# Learned rules (from the founder's past feedback)\n" + ("\n".join(f"- {r}" for r in rules) or "- none yet"))
    if avoid:
        parts.append("# Recent titles - do NOT repeat these ideas\n" + "\n".join(f"- {t}" for t in avoid))
    if redo:
        parts.append(f"# REDO\nThe founder rejected this script:\n{json.dumps(redo['script'], ensure_ascii=False)}\n"
                     f"Reason: {redo['reason']}\nWrite a clearly better version that fixes this.")
    return "\n\n".join(parts)


def _sample_script(plan: dict, product: Product, scenes: int) -> str:
    """Realistic sample used in dry-run mode (no API key), so the flow is testable for free."""
    if not plan.get("letter") and "alpha" not in product.slug:
        return json.dumps({
            "title": f"[Sample] {plan['pillar_name']}: {product.name}",
            "hook": f"{plan['pillar_idea'] or product.name}",
            "scenes": [{"seconds": 8, "veo_prompt": f"Scene {i} showing {product.name}: {plan['pillar_idea']}, bright, 9:16",
                        "on_screen_text": t, "voiceover": t}
                       for i, t in enumerate([f"Meet {product.name}", plan["pillar_name"], f"Try {product.name} today"], 1)][:scenes],
            "end_card": f"Download {product.name}", "caption": f"{plan['pillar_name']} with {product.name}.",
            "hashtags": [f"#{product.slug.replace('-', '')}"], "music_mood": "upbeat",
        })
    letter = plan.get("letter", "A")
    word = plan.get("word") or {"A": "Apple", "B": "Bear", "C": "Cat", "D": "Dolphin", "E": "Elephant"}.get(letter, f"{letter}-word")
    return json.dumps({
        "title": f"[Sample] {plan['pillar_name']}: {letter} is for {word}",
        "hook": f"What does the letter {letter} turn into?",
        "scenes": [
            {"seconds": 8, "veo_prompt": f"Close-up of a glowing wooden letter {letter} card on a sunny kids' table, magical sparkles, soft morning light, 9:16, Pixar-like 3D style",
             "on_screen_text": f"What does {letter} turn into?", "voiceover": f"Watch the letter {letter}..."},
            {"seconds": 8, "veo_prompt": f"A friendly cartoon {word.lower()} pops out of the card in 3D augmented reality, bright colours, gentle bounce, camera slowly orbits, 9:16",
             "on_screen_text": f"{letter} is for {word}!", "voiceover": f"{letter} is for {word}!"},
            {"seconds": 8, "veo_prompt": f"A phone screen held by adult hands showing the AR {word.lower()} waving, warm living room, shallow depth of field, 9:16",
             "on_screen_text": "Learn ABC with magic", "voiceover": f"Try {product.name} today."},
        ][:scenes],
        "end_card": f"Download {product.name} - link in bio",
        "caption": f"{letter} is for {word}! Watch letters come alive in AR with {product.name}.",
        "hashtags": ["#AlphaMagicAR", "#LearnABC", "#KidsLearning", "#ARforKids", f"#Letter{letter}"],
        "music_mood": "playful ukulele",
    })


def write_script(product: Product, slot: str = "any", redo_of: Task | None = None) -> Task:
    agent = AgentRuntime(product.tenant, "marketing")
    agent.ensure_active()
    scenes = int(agent.card.config.get("max_scenes", 3))

    if redo_of is not None:
        plan = dict(redo_of.payload)
        redo = {"script": redo_of.result, "reason": redo_of.approval.reason}
    else:
        plan = plan_next(product, slot)
        redo = None

    label = f"{plan['pillar_name']}" + (f" - {plan['letter']}" if plan.get("letter") else "") + (f" for {plan['word']}" if plan.get("word") else "")
    task = Task.objects.create(tenant=product.tenant, product=product, agent_key="marketing", kind="short_script",
                               title=f"Script: {label}", status="running", payload=plan, parent=redo_of)
    agent.log("task_started", f"Writing script for {product.name}: {label}" + (" (redo)" if redo else ""), task=task)

    avoid = list(Task.objects.filter(product=product, kind="short_script", status__in=["awaiting_approval", "approved", "done"])
                 .values_list("result__title", flat=True)[:15])
    messages = [
        {"role": "system", "content": SYSTEM.format(tenant=product.tenant.name, min_s=15, max_s=30, scenes=scenes)},
        {"role": "user", "content": _user_prompt(product, plan, agent.rules(product), [a for a in avoid if a], redo)},
    ]
    try:
        raw = agent.think(messages, task=task, json_mode=True, mock=_sample_script(plan, product, scenes))
        from agents.llm import parse_json
        script = parse_json(raw)
        missing = [k for k in REQUIRED if not script.get(k)]
        if missing:
            raise ValueError(f"Script missing {', '.join(missing)}")
    except Exception as exc:
        task.status = "failed"
        task.result = {"error": str(exc)}
        task.save()
        agent.log("task_failed", f"Script failed: {exc}", task=task)
        raise

    task.result = script
    task.title = f"Short: {script['title']}"[:200]
    task.status = "awaiting_approval"
    task.save()
    Approval.objects.create(tenant=product.tenant, task=task)
    agent.log("awaiting_approval", f"Script ready for review: {script['title']}", task=task)
    telegram.notify_script(task)
    return task


def daily_run(slot: str) -> list[Task]:
    """Called by the schedule: one script per marketing-enabled product, every active tenant."""
    out = []
    for product in Product.objects.filter(config__marketing=True, status="live").select_related("tenant"):
        per_day = int((product.config or {}).get("shorts_per_day", 2))
        if per_day <= 0 or (slot == "evening" and per_day < 2):
            continue
        try:
            out.append(write_script(product, slot=slot))
        except Exception:
            continue  # already logged; one product failing never blocks the others
    return out
