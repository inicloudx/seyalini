"""Why so few views? The Analyst compares our recent Shorts with the Scout's most-viewed Shorts,
watches our latest ones, and says what is holding them back, with 3 concrete fixes.

- Fixes for the Marketing agent become rules it follows in every new script, but only after you say yes.
- Fixes for you (a setting, an upload test) are listed for you to do.
Honest by design: it never suggests mislabelling content made for children, buying views, fake
engagement or misleading titles.
"""
import json
from datetime import timedelta

from django.utils import timezone

from agents.llm import parse_json, token_cost
from agents.runtime import AgentRuntime
from core.models import Approval, Product, Rule, Task

RECENT = 8          # our Shorts it looks at
WATCH = 2           # of those, how many Gemini watches (public ones only)

DIAG_SYSTEM = """You are the Analyst of {tenant}'s AI marketing team, a world-class YouTube Shorts growth expert.
The app: {app}. The goal is installs by PARENTS (and teachers), through YouTube Shorts.
Our recent Shorts get very few views. Find the real reasons and the fixes.

How to read the numbers:
- 0-20 views after a day means YouTube is barely showing the Short at all: look at distribution first
  (Made for Kids label, upload path, privacy, posting consistency, a new channel, titles and topics YouTube
  cannot place), not only at the content.
- Compare our hooks, length, pace, titles and topics with the top Shorts in our space.

Hard rules for your advice: content aimed at children must stay Made for Kids (never suggest mislabelling);
no buying views, fake engagement, clickbait that lies, or copying other creators' content.

Reply with ONLY a JSON object:
{{"summary": "one or two plain sentences: the main reason our Shorts are not seen",
  "causes": [{{"cause": "short name", "evidence": "the fact from the data that shows it"}}],
  "fixes": [{{"who": "marketing" or "you", "fix": "one clear instruction", "why": "one line"}}],
  "test": "one experiment to run this week that proves or disproves the main cause"}}
Give 2-4 causes, most important first, and EXACTLY 3 fixes. A "marketing" fix is a rule the script-writing
agent will follow in every future script (write it as an instruction to that agent). A "you" fix is something
only the owner can do (a setting, an upload test, the channel page)."""

WATCH_PROMPT = """You are a YouTube Shorts growth expert. Watch this Short from "{app}" ("{title}", {views} views)
and say honestly why viewers would swipe away. Reply with ONLY a JSON object:
{{"first_2_seconds": "what a viewer sees and hears", "swipe_risk": "the moment and reason people leave",
  "pace": "how it moves", "clarity": "is it instantly clear what this is and who it is for",
  "best_fix": "the one change that would keep more viewers"}}"""


def _script_of(video: Task | None) -> Task | None:
    s = video.parent if video and video.parent and video.parent.kind == "short_script" else None
    return s


def _our_shorts(product: Product) -> list[dict]:
    from agents.publisher.youtube_publisher import made_for_kids

    now = timezone.now()
    out = []
    posts = Task.objects.filter(product=product, kind="publish_youtube", status="done").select_related("parent", "parent__parent")
    for p in list(posts)[:RECENT]:
        r, video = p.result or {}, p.parent
        script = _script_of(video)
        s, v = (script.result if script else {}) or {}, (video.result if video else {}) or {}
        stats = r.get("stats") or {}
        out.append({
            "title": p.title.replace("YouTube: ", ""), "url": r.get("url"), "video_id": r.get("video_id"),
            "views": stats.get("views"), "likes": stats.get("likes"), "age_days": round((now - p.created).total_seconds() / 86400, 1),
            "posted_local": timezone.localtime(p.created).strftime("%a %H:%M"),
            "uploaded_as": r.get("privacy"), "privacy_now": stats.get("privacy"),
            "made_for_kids": r.get("made_for_kids", made_for_kids(video) if video else None),
            "pillar": (script.payload or {}).get("pillar") if script else None, "pattern": s.get("pattern"),
            "hook": s.get("hook"), "seconds": v.get("seconds"), "voice": v.get("voice"), "hashtags": s.get("hashtags"),
            "practice": r.get("practice", False),
        })
    return [o for o in out if not o["practice"]]


def _their_shorts(product: Product) -> dict:
    sc = (product.config or {}).get("scout") or {}
    studies = [{"title": t.payload.get("title"), "views_per_day": t.payload.get("views_per_day"), "seconds": t.payload.get("seconds"),
                "hook": (t.result or {}).get("hook"), "why_it_works": (t.result or {}).get("why_it_works")}
               for t in Task.objects.filter(product=product, kind="scout_video", status="done")[:8]]
    return {"summary": sc.get("summary"), "patterns": [p.get("name") for p in sc.get("patterns") or []], "top_shorts": studies}


def _watch(agent: AgentRuntime, product: Product, short: dict, task: Task) -> dict | None:
    """Gemini watches our own public Short from its YouTube link. None when it can't."""
    if agent.dry_run or not short.get("url") or short.get("privacy_now") != "public":
        return None
    model = agent.card.model.split("/")[-1]
    try:
        from google.genai import types

        from tools.genai_client import client

        agent.ensure_budget(0.02)
        resp = client(agent.api_key()).models.generate_content(
            model=model,
            contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=short["url"])),
                                          types.Part(text=WATCH_PROMPT.format(app=product.name, title=short["title"],
                                                                              views=short.get("views") or 0))]),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        usage = resp.usage_metadata
        tin, tout = getattr(usage, "prompt_token_count", 0) or 0, getattr(usage, "candidates_token_count", 0) or 0
        agent.log("llm_call", f"{model} watched our Short: {tin + tout} tokens", task=task,
                  cost=token_cost(model, tin, tout), tokens=tin + tout)
        return parse_json(resp.text or "")
    except Exception as exc:  # never lose the check: think from the numbers instead
        agent.log("analyst_note", f"Could not watch “{short['title'][:60]}” ({str(exc)[:120]})", task=task)
        return None


def _sample(ours: list[dict]) -> dict:
    return {"summary": "YouTube is barely showing these Shorts: all are labelled Made for Kids and the channel is new.",
            "causes": [{"cause": "Made for Kids on every Short", "evidence": f"{len(ours)} Shorts, all made_for_kids, 1-2 views each"},
                       {"cause": "Slow first 2 seconds", "evidence": "Top Shorts open with motion and a question; ours open on a title"}],
            "fixes": [{"who": "marketing", "fix": "Open every Short with movement and a question in the first 2 seconds, no title card.",
                       "why": "Top Shorts in our space all do this."},
                      {"who": "marketing", "fix": "Keep every Short under 20 seconds.", "why": "Top Shorts are 12-20 s; ours are 27 s."},
                      {"who": "you", "fix": "Upload the next Short yourself from the YouTube app as a test.",
                       "why": "Shows whether the upload path is holding views back."}],
            "test": "Post 3 parent-facing Shorts this week and compare their views with the last 3."}


def diagnose(product: Product, note: str = "", redo_of: Task | None = None) -> Task:
    tenant = product.tenant
    agent = AgentRuntime(tenant, "analyst")
    agent.ensure_active()
    task = Task.objects.create(tenant=tenant, product=product, agent_key="analyst", kind="view_diagnosis", parent=redo_of,
                               title=f"Why so few views: {product.name}", status="running", payload={"note": note})
    try:
        ours = _our_shorts(product)
        if not ours:
            raise ValueError("No Shorts posted to YouTube yet, so there is nothing to check.")
        watched = []
        for short in [s for s in ours if s.get("privacy_now") == "public"][:WATCH]:
            if notes := _watch(agent, product, short, task):
                watched.append({"title": short["title"], **notes})
        facts = {"our_recent_shorts": ours, "we_watched_our_shorts": watched, "top_shorts_in_our_space": _their_shorts(product),
                 "app_default_made_for_kids": bool((product.config or {}).get("youtube_made_for_kids")),
                 "content_ideas": [{k: p.get(k) for k in ("key", "name", "idea", "audience")} for p in (product.config or {}).get("pillars") or []],
                 "rules_marketing_already_follows": agent.rules(product) + AgentRuntime(tenant, "marketing").rules(product)}
        user = "# Facts\n" + json.dumps(facts, ensure_ascii=False, default=str)[:30000]
        if note:
            user += f"\n# The owner asks you to look again, and says: {note}"
        raw = agent.think([{"role": "system", "content": DIAG_SYSTEM.format(tenant=tenant.name, app=product.name)},
                           {"role": "user", "content": user}], task=task, json_mode=True, mock=json.dumps(_sample(ours)))
        out = parse_json(raw)
        fixes = [f for f in out.get("fixes") or [] if isinstance(f, dict) and f.get("fix")][:3]
        for f in fixes:
            f["who"] = "you" if f.get("who") == "you" else "marketing"
            f["fix"] = str(f["fix"])[:280]
        if not fixes:
            raise ValueError("the check came back without fixes")
        task.result = {"summary": out.get("summary", ""), "causes": (out.get("causes") or [])[:4], "fixes": fixes,
                       "test": out.get("test", ""), "checked": len(ours), "watched": len(watched)}
        task.status = "awaiting_approval"
        task.save()
        Approval.objects.create(tenant=tenant, task=task)
        agent.log("awaiting_approval", f"Views check for {product.name}: {task.result['summary']}"[:300], task=task)
    except Exception as exc:
        task.status, task.result = "failed", {"error": str(exc)[:300]}
        task.save()
        agent.log("task_failed", f"Views check failed: {str(exc)[:200]}", task=task)
        from agents.manager import chat as manager

        manager.notify(tenant, f"⚠️ I couldn't check views for {product.name}: {str(exc)[:200]}")
        return task
    from agents.manager import chat as manager

    manager.ask_diagnosis(task)
    return task


def apply_fixes(task: Task, which: list[int] | None = None) -> list[Rule]:
    """You said yes: the chosen Marketing fixes become rules for every new script (1-based numbers; None = all)."""
    fixes = (task.result or {}).get("fixes") or []
    chosen = [f for i, f in enumerate(fixes, 1) if which is None or i in which]
    rules = [Rule.objects.create(tenant=task.tenant, agent_key="marketing", product=task.product, text=f["fix"][:300],
                                 source="analyst")
             for f in chosen if f.get("who") == "marketing"]
    task.result = {**(task.result or {}), "applied": [fixes.index(f) + 1 for f in chosen]}
    task.status = "done"
    task.save(update_fields=["result", "status", "updated"])
    AgentRuntime(task.tenant, "analyst").log("fixes_applied", f"{len(rules)} new rule{'s' if len(rules) != 1 else ''} for the "
                                                              f"Marketing agent from the views check", task=task)
    return rules


def is_recent(product: Product, hours=1) -> bool:
    return Task.objects.filter(product=product, kind="view_diagnosis", created__gte=timezone.now() - timedelta(hours=hours),
                               status__in=["running", "awaiting_approval"]).exists()
