"""Scout agent: studies the most successful Shorts in our space, so every script starts
from a pattern that already works for millions of viewers.

1. Find: YouTube search for each of the app's search terms (short videos from the last
   30 days), ranked by views per day, so fresh hits beat old giants. 100 quota units per
   term; YouTube gives 10,000 free a day.
2. Watch: Gemini watches the top new ones straight from their YouTube link and notes the
   hook, structure, pace, on-screen text, voice/music, ending and why it works. If it
   cannot watch a video, it studies the title and description instead (never lose a run).
3. Playbook: turns the recent notes into a few proven patterns for this app. The planner
   hands one pattern to each new script; you see which one when you approve.
"""
import json
from datetime import datetime, timedelta

from django.utils import timezone

from agents.llm import parse_json, token_cost
from agents.runtime import AgentRuntime
from core.models import Product, Task
from core.secrets import get_secret
from tools import youtube

DEFAULT_QUERIES = ["abc song for kids", "alphabet learning for toddlers", "phonics for kids", "AR app for kids"]
DAYS = 30               # only recent hits: what works NOW
MAX_SECONDS = 180       # YouTube Shorts are up to 3 minutes
WATCH_PER_RUN = 5       # new videos studied per app per run (~$0.01 each)
KEEP = 30               # latest studies used for the playbook
TOP_SHOWN = 5

WATCH_PROMPT = """You study successful YouTube Shorts for the marketing team of "{app}", {about}.
Watch this Short ("{title}" by {channel}, {views:,} views) and explain why it works.
Reply with ONLY a JSON object:
{{"hook": "exactly what happens and is said/shown in the first 2 seconds",
  "hook_type": "one of: question, surprise reveal, challenge, before-after, POV, satisfying, funny, direct talk, song",
  "structure": "the beats in order, one short line",
  "pace": "how fast it cuts and moves",
  "on_screen_text": "style and amount of text on screen",
  "audio": "voice, music or trending sound, and energy",
  "ending": "how it ends, any call to action or loop",
  "why_it_works": "the real reason people watch to the end, one or two sentences",
  "pattern_for_us": "how {app} could use this same pattern, one sentence"}}"""

META_PROMPT = """You study successful YouTube Shorts for the marketing team of "{app}", {about}.
You cannot watch this Short, only read about it. Infer what you can.
Title: {title}
Channel: {channel}
Views: {views:,} in {age} days, length {seconds}s
Description: {description}
Tags: {tags}
Reply with ONLY the JSON object described here, marking guesses with "(guess)":
{{"hook": str, "hook_type": str, "structure": str, "pace": str, "on_screen_text": str, "audio": str,
  "ending": str, "why_it_works": str, "pattern_for_us": str}}"""

PLAYBOOK_SYSTEM = """You are the Scout of {tenant}'s marketing team. From notes on the most-viewed recent
Shorts in our space, write a playbook for "{app}" ({about}).
The goal is installs: videos parents watch to the end and then download the app.
Keep it kind and safe for families. No copying other creators' characters, songs or brands:
copy the PATTERN, never the content.
Reply with ONLY a JSON object:
{{"summary": "one plain sentence: what is working on YouTube right now",
  "patterns": [{{"name": "short name", "recipe": "how to make it, 1-2 sentences",
                 "hook_example": "a first line for OUR app using this pattern", "based_on": ["title", ...]}}]}}
Give {n} patterns, strongest first."""


def _queries(product: Product, agent: AgentRuntime) -> list[str]:
    return (product.config or {}).get("scout_queries") or (agent.card.config or {}).get("queries") or DEFAULT_QUERIES


def _about(product: Product) -> str:
    cfg = product.config or {}
    return cfg.get("scout_about") or (product.brief or "")[:300].replace("\n", " ") or product.name


def find(product: Product, token: str, agent: AgentRuntime) -> list[dict]:
    """Top recent Shorts for this app's search terms, best first (views per day)."""
    own = ((product.tenant.settings or {}).get("youtube") or {}).get("channel_id")
    found: dict[str, str] = {}
    for q in _queries(product, agent):
        for vid in youtube.search_shorts(token, q, days=DAYS):
            found.setdefault(vid, q)
    now = timezone.now()
    out = []
    for d in youtube.video_details(token, list(found)):
        if not d["seconds"] or d["seconds"] > MAX_SECONDS or (own and d["channel_id"] == own):
            continue
        try:
            published = datetime.fromisoformat(d["published"].replace("Z", "+00:00"))
        except ValueError:
            published = now
        age = max(1.0, (now - published).total_seconds() / 86400)
        out.append({**d, "age_days": round(age), "views_per_day": int(d["views"] / age), "query": found[d["id"]]})
    out.sort(key=lambda d: -d["views_per_day"])
    agent.log("scout_found", f"{product.name}: {len(out)} Shorts found for {len(_queries(product, agent))} search terms")
    return out


def _sample_notes(video: dict) -> dict:
    """Dry run (no AI key): realistic notes so the whole flow is testable for free."""
    return {"hook": f"Big bright question on screen: what comes next? ({video['title'][:40]})",
            "hook_type": "question", "structure": "question, slow build, big reveal, loop back",
            "pace": "a new shot every 1-2 seconds", "on_screen_text": "3-4 big words per shot",
            "audio": "cheerful sing-along with a clear voice", "ending": "loops straight into the start",
            "why_it_works": "Kids and parents want to see the reveal, so they watch to the end.",
            "pattern_for_us": "Ask which object a letter becomes, then reveal it in AR.", "sample": True}


def _watch(agent: AgentRuntime, product: Product, video: dict, task: Task) -> dict:
    """Gemini watches the Short from its YouTube link. Falls back to reading its title and description."""
    if agent.dry_run:
        return _sample_notes(video)
    model = agent.card.model.split("/")[-1]
    prompt = WATCH_PROMPT.format(app=product.name, about=_about(product), title=video["title"],
                                 channel=video["channel"], views=video["views"])
    try:
        from google.genai import types

        from tools.genai_client import client

        agent.ensure_budget(0.02)
        resp = client(agent.api_key()).models.generate_content(
            model=model,
            contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=video["url"])),
                                          types.Part(text=prompt)]),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        usage = resp.usage_metadata
        tin, tout = getattr(usage, "prompt_token_count", 0) or 0, getattr(usage, "candidates_token_count", 0) or 0
        agent.log("llm_call", f"{model} watched a Short: {tin + tout} tokens", task=task,
                  cost=token_cost(model, tin, tout), tokens=tin + tout)
        notes = parse_json(resp.text or "")
        notes["watched"] = True
        return notes
    except Exception as exc:
        agent.log("scout_note", f"Could not watch “{video['title'][:60]}” ({str(exc)[:120]}); reading its details instead", task=task)
    raw = agent.think([{"role": "user", "content": META_PROMPT.format(
        app=product.name, about=_about(product), title=video["title"], channel=video["channel"], views=video["views"],
        age=video.get("age_days", "?"), seconds=video["seconds"], description=video.get("description", ""),
        tags=", ".join(video.get("tags") or []))}], task=task, json_mode=True, mock=json.dumps(_sample_notes(video)))
    notes = parse_json(raw)
    notes["watched"] = False
    return notes


def study(product: Product, ranked: list[dict], agent: AgentRuntime) -> int:
    """Watch the best new Shorts (ones not studied before)."""
    seen = set(Task.objects.filter(product=product, kind="scout_video").values_list("payload__video_id", flat=True))
    fresh = [v for v in ranked if v["id"] not in seen][:WATCH_PER_RUN]
    for v in fresh:
        payload = {k: v.get(k) for k in ("title", "channel", "url", "views", "views_per_day", "seconds", "age_days", "query")}
        task = Task.objects.create(tenant=product.tenant, product=product, agent_key="scout", kind="scout_video",
                                   title=f"Studied: {v['title']}"[:200], status="running", payload={"video_id": v["id"], **payload})
        try:
            task.result = _watch(agent, product, v, task)
            task.status = "done"
        except Exception as exc:
            task.result, task.status = {"error": str(exc)[:300]}, "failed"
            agent.log("task_failed", f"Scout could not study “{v['title'][:60]}”: {str(exc)[:150]}", task=task)
        task.save()
    return len(fresh)


def build_playbook(product: Product, ranked: list[dict], agent: AgentRuntime) -> dict:
    studies = list(Task.objects.filter(product=product, kind="scout_video", status="done")[:KEEP])
    if not studies:
        return {}
    notes = [{"title": s.payload.get("title"), "views_per_day": s.payload.get("views_per_day"), **s.result} for s in studies]
    n = int((agent.card.config or {}).get("patterns", 5))
    sample = json.dumps({"summary": "Short question-and-reveal videos with sing-along audio are winning this month.",
                         "patterns": [{"name": "Guess the reveal", "recipe": "Ask what a letter turns into, pause, then reveal it in AR.",
                                       "hook_example": "What does the letter D turn into?", "based_on": [notes[0]["title"]]}]})
    raw = agent.think([
        {"role": "system", "content": PLAYBOOK_SYSTEM.format(tenant=product.tenant.name, app=product.name, about=_about(product), n=n)},
        {"role": "user", "content": "# Notes on top Shorts (best first)\n" + json.dumps(notes, ensure_ascii=False)[:30000]},
    ], json_mode=True, mock=sample)
    book = parse_json(raw)
    by_id = {s.payload.get("video_id"): s for s in studies}
    top = []
    for v in ranked[:TOP_SHOWN]:
        s = by_id.get(v["id"])
        top.append({"title": v["title"], "channel": v["channel"], "url": v["url"], "views": v["views"],
                    "views_per_day": v["views_per_day"], "hook": (s.result or {}).get("hook", "") if s else "",
                    "why": (s.result or {}).get("why_it_works", "") if s else ""})
    scout = {"updated": timezone.now().isoformat(timespec="minutes"), "summary": book.get("summary", ""),
             "patterns": [p for p in book.get("patterns") or [] if p.get("name") and p.get("recipe")][:n], "top": top}
    cfg = dict(product.config or {})
    cfg["scout"] = scout
    product.config = cfg
    product.save(update_fields=["config"])
    agent.log("scout_playbook", f"{product.name}: {len(scout['patterns'])} proven patterns. {scout['summary']}"[:300])
    return scout


def run(tenant) -> int:
    """Daily: for every app we market, find, study and update the playbook. Returns Shorts studied."""
    agent = AgentRuntime(tenant, "scout")
    agent.ensure_active()
    token = youtube.access_token(get_secret(tenant, "YOUTUBE_CLIENT_ID"), get_secret(tenant, "YOUTUBE_CLIENT_SECRET"),
                                 get_secret(tenant, "YOUTUBE_REFRESH_TOKEN"))
    studied = 0
    for product in Product.objects.filter(tenant=tenant, status="live", config__marketing=True):
        try:
            ranked = find(product, token, agent)
            studied += study(product, ranked, agent)
            build_playbook(product, ranked, agent)
        except Exception as exc:  # one app failing never blocks the others
            agent.log("task_failed", f"Scout run for {product.name} failed: {str(exc)[:200]}")
    agent.log("scout_run", f"Scout studied {studied} new Short{'s' if studied != 1 else ''}")
    return studied


def is_due(tenant, hours=20) -> bool:
    from core.models import Event

    last = Event.objects.filter(tenant=tenant, agent_key="scout", kind="scout_run").order_by("-created").first()
    return last is None or last.created < timezone.now() - timedelta(hours=hours)
