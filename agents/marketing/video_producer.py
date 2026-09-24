"""Turns an APPROVED script into a finished vertical video for your review.

Video modes (set in the Marketing agent card, `config.video.mode`):
  images - AI still images + slow zoom          ~ $0.12 per Short  (default)
  hybrid - Veo clip for the hook, images after  ~ $0.50 per Short
  veo    - Veo clip for every scene             ~ $1.20+ per Short
Real app recordings in the product's assets/clips/ folder (or the store screenshots
saved during onboarding) are used for "Watch the Magic" scenes for free.
"""
import random
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from agents.runtime import AgentRuntime
from core.models import Approval, Task
from tools import editor, imagegen, telegram, veo
from tools.fonts import use_brand_fonts

DEFAULTS = {
    "mode": "images",
    "image_model": "gemini-2.5-flash-image",
    "image_usd": 0.04,
    "veo_model": "veo-3.1-lite-generate-preview",
    "veo_usd_per_second": 0.05,
    "veo_resolution": "720p",
    "end_card_seconds": 3,
    "library_pillars": ["watch_the_magic"],
}
STYLE = ("Vertical 9:16 frame. Bright, colourful, friendly 3D animated style for young children, soft lighting, "
         "clean composition with empty space in the lower third for captions. No written text, no logos, no real people.")


def _cfg(agent):
    return {**DEFAULTS, **(agent.card.config or {}).get("video", {})}


def product_assets(product) -> Path:
    return settings.TENANTS_DIR / product.tenant.slug / "products" / product.slug / "assets"


def plan_sources(script: dict, mode: str, pillar: str, cfg: dict, assets: Path) -> list[str]:
    n = len(script.get("scenes", []))
    if mode == "veo":
        kinds = ["veo"] * n
    elif mode == "hybrid":
        kinds = ["veo"] + ["image"] * (n - 1)
    else:
        kinds = ["image"] * n
    if pillar in cfg["library_pillars"] and library_files(assets) and n >= 2:
        kinds[1] = "library"  # middle scene = real app footage or a store screenshot (free)
    return kinds


def library_files(assets: Path) -> list[Path]:
    """Your own screen recordings first; store screenshots saved by onboarding as a fallback."""
    clips = sorted((assets / "clips").glob("*.mp4")) if (assets / "clips").exists() else []
    if clips:
        return clips
    screens = assets / "screens"
    return sorted(screens.glob("*.png")) + sorted(screens.glob("*.jpg")) if screens.exists() else []


def estimate(kinds: list[str], script: dict, cfg: dict) -> Decimal:
    total = Decimal("0")
    for kind, sc in zip(kinds, script["scenes"]):
        if kind == "image":
            total += Decimal(str(cfg["image_usd"]))
        elif kind == "veo":
            total += Decimal(str(cfg["veo_usd_per_second"])) * int(sc.get("seconds", 8))
    return total


def produce_video(script_task: Task, redo_of: Task | None = None) -> Task:
    product = script_task.product
    agent = AgentRuntime(product.tenant, "marketing")
    agent.ensure_active()
    cfg = _cfg(agent)
    script = script_task.result
    assets = product_assets(product)
    dry = agent.dry_run
    gemini_key = agent.api_key("GEMINI_API_KEY")
    kinds = plan_sources(script, cfg["mode"], script_task.payload.get("pillar", ""), cfg, assets)
    est = estimate(kinds, script, cfg)

    task = Task.objects.create(
        tenant=product.tenant, product=product, agent_key="marketing", kind="short_video",
        title=f"Video: {script.get('title', '')}"[:200], status="running", parent=redo_of or script_task,
        payload={"script_task": script_task.id, "mode": cfg["mode"], "sources": kinds, "estimate_usd": float(est)},
    )
    try:
        agent.ensure_budget(0 if dry else est)
        agent.log("task_started", f"Producing video ({cfg['mode']}, ~${est:.2f}): {script.get('title')}", task=task)
        rules = agent.rules(product)
        feedback = ""
        if rules:
            feedback = " Founder feedback to respect: " + "; ".join(rules[-8:]) + "."
        if redo_of is not None and redo_of.approval.reason:
            feedback += f" The previous version was rejected because: {redo_of.approval.reason}. Fix that."

        out_dir = Path(settings.MEDIA_ROOT) / "videos" / product.tenant.slug / str(task.id)
        use_brand_fonts(assets / "fonts")
        accent = (product.config or {}).get("accent") or (product.tenant.theme or {}).get("accent", "#C2410C")
        visual_style = ((product.config or {}).get("visual_style") or "").strip()
        visual_style = f"{visual_style} " if visual_style else ""
        scenes = []
        for i, (kind, sc) in enumerate(zip(kinds, script["scenes"])):
            prompt = f"{sc.get('veo_prompt', '')}. {visual_style}{STYLE}{feedback}"
            secs = float(sc.get("seconds", 8))
            if kind == "library":
                src = random.choice(library_files(assets))
                agent.log("asset_used", f"Scene {i + 1}: real app footage {src.name} (free)", task=task)
            elif kind == "veo" and not dry:
                src = veo.generate(prompt, out_dir / f"scene{i}.mp4", model=cfg["veo_model"], api_key=gemini_key,
                                   seconds=int(secs), resolution=cfg["veo_resolution"])
                agent.spend("video_gen", f"Scene {i + 1}: Veo {int(secs)}s", Decimal(str(cfg["veo_usd_per_second"])) * int(secs), task=task)
            else:
                src = imagegen.generate(prompt, out_dir / f"scene{i}.png", model=cfg["image_model"], dry_run=dry, api_key=gemini_key,
                                        label=f"Scene {i + 1} ({kind}): {sc.get('on_screen_text', '')}", accent=accent)
                agent.spend("image_gen", f"Scene {i + 1}: image" + (" (dry run)" if dry else ""), 0 if dry else cfg["image_usd"], task=task)
            scenes.append(editor.Scene(Path(src), secs, sc.get("on_screen_text", "")))

        logo = next((p for p in (assets / "logo.png", assets / "logo.jpg") if p.exists()), None)
        info = editor.render(
            scenes, out_dir / "final.mp4", badge=product.name, accent=accent, app_name=product.name,
            cta=script.get("end_card") or f"Download {product.name}", logo=logo, music_dir=assets / "music",
            end_seconds=float(cfg["end_card_seconds"]),
        )
    except Exception as exc:
        task.status = "failed"
        task.result = {"error": str(exc)[:500]}
        task.save()
        agent.log("task_failed", f"Video failed: {str(exc)[:200]}", task=task)
        raise

    rel = lambda p: str(Path(p).relative_to(settings.MEDIA_ROOT)).replace("\\", "/")
    task.result = {"title": script.get("title"), "video": rel(out_dir / "final.mp4"), "thumbnail": rel(info["thumbnail"]),
                   "seconds": info["seconds"], "music": info["music"], "caption": script.get("caption"),
                   "hashtags": script.get("hashtags", []), "sources": kinds}
    task.status = "awaiting_approval"
    task.save()
    Approval.objects.create(tenant=product.tenant, task=task)
    agent.log("awaiting_approval", f"Video ready for review ({info['seconds']:.0f}s): {script.get('title')}", task=task)
    telegram.notify_video(task, out_dir / "final.mp4")
    return task
