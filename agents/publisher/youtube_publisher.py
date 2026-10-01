"""Publisher agent: posts an approved Short to the organisation's YouTube channel.

Only runs on videos you approved. Without a connected channel it simply waits;
in practice mode (no Gemini key) it pretends, so the whole flow can be tried for free.
"""
from pathlib import Path

from django.conf import settings

from agents.runtime import AgentRuntime
from core.models import Task
from core.secrets import get_secret
from tools import youtube


def is_connected(tenant) -> bool:
    return bool(get_secret(tenant, "YOUTUBE_REFRESH_TOKEN") and get_secret(tenant, "YOUTUBE_CLIENT_ID")
                and get_secret(tenant, "YOUTUBE_CLIENT_SECRET"))


def made_for_kids(video_task: Task) -> bool:
    """Per content idea: a pillar's audience decides (kids -> yes, adults -> no); else the app's default."""
    cfg = video_task.product.config or {}
    script = video_task.parent if video_task.parent and video_task.parent.kind == "short_script" else None
    key = ((script.payload or {}).get("pillar") if script else "") or ""
    audience = next((p.get("audience") for p in cfg.get("pillars") or [] if p.get("key") == key), None)
    if audience in ("kids", "adults"):
        return audience == "kids"
    return bool(cfg.get("youtube_made_for_kids", False))


def build_metadata(video_task: Task) -> dict:
    product = video_task.product
    cfg = product.config or {}
    r = video_task.result or {}
    tags = [h.lstrip("#") for h in r.get("hashtags", []) if h]
    store = cfg.get("store_url", "")
    hashtags = " ".join(r.get("hashtags", [])[:5])
    lines = [r.get("caption") or r.get("title", "")]
    if store:  # the referrer lets Play Console count installs that came from these Shorts
        sep = "&" if "?" in store else "?"
        tracked = f"{store}{sep}referrer=utm_source%3Dyoutube%26utm_medium%3Dshorts%26utm_campaign%3Dseyalini"
        lines += ["", f"Download {product.name}: {tracked}"]
    lines += ["", f"{hashtags} #Shorts".strip()]
    return {"title": (r.get("title") or product.name).replace("[Sample] ", "")[:100], "description": "\n".join(lines),
            "tags": tags + [product.name], "made_for_kids": made_for_kids(video_task)}


def publish(video_task: Task, force: bool = False) -> Task | None:
    tenant = video_task.tenant
    agent = AgentRuntime(tenant, "publisher")
    agent.ensure_active()
    existing = Task.objects.filter(parent=video_task, kind="publish_youtube").exclude(status="failed").first()
    if existing and not force:
        return existing
    if not agent.dry_run and not is_connected(tenant):
        agent.log("skipped", f"Not posted (YouTube not connected): {video_task.result.get('title')}", task=video_task)
        return None

    meta = build_metadata(video_task)
    privacy = (agent.card.config or {}).get("youtube_privacy", "private")
    task = Task.objects.create(tenant=tenant, product=video_task.product, agent_key="publisher", kind="publish_youtube",
                               title=f"YouTube: {meta['title']}"[:200], status="running", parent=video_task,
                               payload={"platform": "youtube", "privacy": privacy})
    try:
        if agent.dry_run:
            vid, channel = "practice-mode", "(practice mode)"
        else:
            token = youtube.access_token(get_secret(tenant, "YOUTUBE_CLIENT_ID"), get_secret(tenant, "YOUTUBE_CLIENT_SECRET"),
                                         get_secret(tenant, "YOUTUBE_REFRESH_TOKEN"))
            path = Path(settings.MEDIA_ROOT) / video_task.result["video"]
            vid = youtube.upload(token, path, title=meta["title"], description=meta["description"], tags=meta["tags"],
                                 privacy=privacy, made_for_kids=meta["made_for_kids"])
            channel = (tenant.settings or {}).get("youtube", {}).get("channel", "")
    except Exception as exc:
        task.status = "failed"
        task.result = {"error": str(exc)[:500]}
        task.save()
        agent.log("task_failed", f"YouTube upload failed: {str(exc)[:200]}", task=task)
        from agents.manager import chat as manager

        manager.notify(tenant, f"⚠️ YouTube upload failed for {meta['title']}: {str(exc)[:200]}\nRetry it in Seyalini → Videos.")
        raise
    url = None if agent.dry_run else f"https://youtube.com/shorts/{vid}"
    task.status = "done"
    task.result = {"platform": "youtube", "video_id": vid, "url": url, "privacy": privacy, "channel": channel,
                   "made_for_kids": meta["made_for_kids"], "practice": agent.dry_run}
    task.save()
    agent.log("published", f"Posted to YouTube ({privacy}){' [practice]' if agent.dry_run else ''}: {meta['title']}", task=task)
    from agents.manager import chat as manager

    manager.posted(task)
    return task
