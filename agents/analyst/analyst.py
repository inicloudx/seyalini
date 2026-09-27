"""Analyst agent: watches how posted Shorts perform and tells the team what works.

1. Collect: views / likes / comments for every Short posted in the last 60 days
   (YouTube Data API, free: 1 quota unit per 50 videos).
2. Learn: compare content ideas (pillars). Ideas that beat the average get a
   higher score, and the planner picks them more often. Plain-language insights
   appear on the dashboard.
Installs per video come next, via the tracked Play Store link in each description.
"""
from datetime import timedelta
from statistics import mean

from django.utils import timezone

from agents.runtime import AgentRuntime
from core.models import Product, Task
from core.secrets import get_secret
from tools import youtube

MIN_AGE_HOURS = 24          # a Short needs a day before its numbers mean anything
MIN_PER_PILLAR = 2          # evidence needed before an idea's score moves


def _posted(tenant, days=60):
    since = timezone.now() - timedelta(days=days)
    return [p for p in Task.objects.filter(tenant=tenant, kind="publish_youtube", status="done", created__gte=since)
            .select_related("parent", "parent__parent", "product")
            if (p.result or {}).get("video_id") and not (p.result or {}).get("practice")]


def collect(tenant) -> int:
    agent = AgentRuntime(tenant, "analyst")
    agent.ensure_active()
    posts = _posted(tenant)
    if not posts:
        return 0
    token = youtube.access_token(get_secret(tenant, "YOUTUBE_CLIENT_ID"), get_secret(tenant, "YOUTUBE_CLIENT_SECRET"),
                                 get_secret(tenant, "YOUTUBE_REFRESH_TOKEN"))
    stats = youtube.video_stats(token, [p.result["video_id"] for p in posts])
    now = timezone.now()
    for p in posts:
        st = stats.get(p.result["video_id"])
        if st is None:
            continue
        history = (p.result.get("history") or [])[-29:] + [{"at": now.isoformat(timespec="minutes"), "views": st["views"]}]
        p.result = {**p.result, "stats": st, "checked": now.isoformat(timespec="minutes"), "history": history}
        p.save(update_fields=["result"])
    total = sum(s["views"] for s in stats.values())
    agent.log("stats_collected", f"Checked {len(stats)} Shorts on YouTube: {total:,} views in total")
    return len(stats)


def _pillar_of(post) -> str:
    video = post.parent
    script = video.parent if video and video.parent and video.parent.kind == "short_script" else None
    while script and script.parent and script.parent.kind == "short_script":
        script = script.parent
    return (script.payload or {}).get("pillar", "") if script else ""


def learn(product: Product) -> list[str]:
    """Score each content idea by views relative to this app's average. Returns insights."""
    cutoff = timezone.now() - timedelta(hours=MIN_AGE_HOURS)
    posts = [p for p in _posted(product.tenant) if p.product_id == product.id and p.created <= cutoff
             and (p.result or {}).get("stats")]
    cfg = dict(product.config or {})
    names = {pl["key"]: pl["name"] for pl in cfg.get("pillars") or []}
    if len(posts) < 3:
        cfg["insights"] = [f"Watching {len(posts)} posted Short{'s' if len(posts) != 1 else ''}. "
                           "Insights start after 3 Shorts are a day old."]
        product.config = cfg
        product.save(update_fields=["config"])
        return cfg["insights"]

    avg = mean(p.result["stats"]["views"] for p in posts) or 1
    by_pillar: dict[str, list[int]] = {}
    for p in posts:
        by_pillar.setdefault(_pillar_of(p) or "other", []).append(p.result["stats"]["views"])
    scores, insights = {}, []
    for key, views in by_pillar.items():
        if len(views) < MIN_PER_PILLAR or key == "other":
            continue
        ratio = mean(views) / avg
        scores[key] = round(max(0.3, min(3.0, ratio)), 2)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if ranked:
        best_key, best = ranked[0]
        insights.append(f"“{names.get(best_key, best_key)}” Shorts get {best:.1f}× the average views. The AI will make more of them.")
        if len(ranked) > 1 and ranked[-1][1] < 0.8:
            low_key, low = ranked[-1]
            insights.append(f"“{names.get(low_key, low_key)}” Shorts get fewer views ({low:.1f}×). The AI will make them less often.")
    top = max(posts, key=lambda p: p.result["stats"]["views"])
    insights.append(f"Best so far: “{top.result.get('title') or top.title.replace('YouTube: ', '')}” with {top.result['stats']['views']:,} views.")
    cfg["pillar_scores"], cfg["insights"] = scores, insights
    product.config = cfg
    product.save(update_fields=["config"])
    AgentRuntime(product.tenant, "analyst").log("insight", insights[0][:300])
    return insights


def run(tenant) -> int:
    n = collect(tenant)
    for product in Product.objects.filter(tenant=tenant, status="live"):
        learn(product)
    return n


def is_due(tenant, hours=6) -> bool:
    from core.models import Event

    last = Event.objects.filter(tenant=tenant, agent_key="analyst", kind="stats_collected").order_by("-created").first()
    return last is None or last.created < timezone.now() - timedelta(hours=hours)
