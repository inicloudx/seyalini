"""Earner agent: thinks like a business builder whose only job is to earn money for the owner,
in ANY field outside what the company already does.

1. Hunt (weekly): searches the web for what people are paying for right now, then proposes a few
   money ideas in different fields, each with cost, time to the first rupee, potential, and exactly
   what the AI does versus what you do. Ideas that break the house rules are dropped before you see them.
2. Plan (when you say "Try it"): a 14-day experiment with a rupee goal, day-by-day steps, a spending
   cap and the first drafts already written (listing, outreach message, landing page copy...).
3. Ledger: you note money in and money out per experiment (the Earner never touches money itself).
4. Review (daily, acts when a test ends): scale what pays, stop what doesn't, and remember why, so the
   next hunt is smarter.

The Earner proposes and drafts. It never spends, signs up, posts or sends anything by itself.
"""
import json
from datetime import datetime, timedelta

from django.utils import timezone

from agents.llm import parse_json, token_cost
from agents.runtime import AgentRuntime
from core.models import Approval, Task

# A safety net under the prompt's house rules: ideas mentioning these never reach you.
BLOCKED = (
    "gambl", "betting", "casino", "lottery", "forex", "crypto trading", "day trading", "options trading",
    "futures trading", "mlm", "multi-level marketing", "pyramid scheme", "ponzi", "fake review", "fake follower",
    "buy followers", "spam", "mass email", "scrape personal", "scraping personal", "adult content", "escort",
    "loan app", "instant loan", "counterfeit", "replica", "plagiar", "essay mill", "homework for pay",
    "phishing", "hacking", "piracy", "pirated", "torrent", "prescription drug",
)
MAX_PAST = 60  # past ideas the Earner is told about, so it does not repeat itself

HOUSE_RULES = """House rules (never break these; an idea that needs them is not an idea):
- Legal in India, taxable income, no grey areas. No gambling, betting, trading or crypto speculation,
  no MLM, no loans, no adult content, no medicines.
- Honest: no spam, fake reviews, fake followers, copied content or scraping people's personal data.
- Never risk more than the experiment budget. Prefer ideas that earn before they spend.
- The owner does not do the work: the AI does most of it and the owner only reviews, approves,
  creates accounts and receives payments."""

HUNT_SYSTEM = """You are the Earner of {tenant}: a world-class business builder whose ONLY job is to earn
real money for the owner. You are not limited to any sector. Think like a sharp entrepreneur: where are
people already paying, what is under-served, what can an AI agent team do cheaply that others charge for?

About the owner: {about}

Stay OUT of the fields the owner already works in: {avoid}. Find money elsewhere.
Spread your ideas across different fields and different ways of earning (selling a product, a service,
a subscription, commissions, licensing, rentals...). Prefer:
- first rupee within {first_days} days, starting cost at most Rs {budget}
- work the AI can do (writing, design, research, code, video, customer replies)
- at most {hours} hours a week of the owner's time
- things that can grow without the owner's time growing

{rules}
{lessons}{learned}{past}{note}
Reply with ONLY a JSON object:
{{"ideas": [{{"name": "short name", "field": "the sector",
  "how_it_makes_money": "who pays, for what, how much, through which platform",
  "why_now": "the demand signal you saw, one sentence",
  "first_rupee_days": 7, "startup_cost_inr": 500,
  "monthly_low_inr": 2000, "monthly_high_inr": 20000, "owner_hours_per_week": 2,
  "ai_does": ["what the AI team does"], "you_do": ["what the owner must do, e.g. create the seller account"],
  "risks": "the honest downside", "score": 7}}]}}
Give {n} ideas, best first. score = your confidence out of 10 that it earns within 30 days."""

PLAN_SYSTEM = """You are the Earner of {tenant}, a world-class business builder. The owner said "try it"
to this money idea. Turn it into a {days}-day experiment that proves or kills it cheaply.
About the owner: {about}
{rules}
Spending cap for the whole experiment: Rs {budget}. The AI team (you) must do most of the work.
Write the first drafts NOW, ready to copy and paste (the product listing, first outreach message,
landing page text, price list... whatever this idea needs first).
Reply with ONLY a JSON object:
{{"goal": "the one number that proves it works, e.g. Rs 3,000 earned or 5 paying customers",
  "goal_inr": 3000, "budget_inr": 1000,
  "steps": [{{"day": 1, "who": "you" or "earner", "action": "exactly what to do"}}],
  "drafts": [{{"title": "what it is", "text": "the ready-to-use text"}}],
  "stop_if": "the signal to stop early", "scale_if": "the signal to put more in"}}"""

REVIEW_SYSTEM = """You are the Earner of {tenant}, a world-class business builder. A money experiment has
ended. Decide like an investor: scale it (it pays or clearly will), stop it (it does not), or extend it
once (only if the signal is promising but too early). Be honest; sunk cost does not count.
Reply with ONLY a JSON object:
{{"verdict": "scale" or "stop" or "extend", "why": "one or two plain sentences",
  "lesson": "what this teaches the next hunt, one sentence",
  "next_steps": ["if scaling or extending: the next concrete actions"]}}"""


# --- helpers -------------------------------------------------------------------------------------

def _cfg(agent: AgentRuntime) -> dict:
    return agent.card.config or {}


def is_blocked(idea: dict) -> str:
    """The rule an idea breaks, or "" if it is fine."""
    text = json.dumps(idea, ensure_ascii=False).lower()
    return next((word for word in BLOCKED if word in text), "")


def _int(value, default=0) -> int:
    try:
        return int(float(str(value).replace(",", "").replace("Rs", "").replace("₹", "").strip()))
    except (TypeError, ValueError):
        return default


def ledger_totals(plan: Task) -> dict:
    entries = (plan.result or {}).get("ledger") or []
    earned = sum(e["amount_inr"] for e in entries if e["kind"] == "earned")
    spent = sum(e["amount_inr"] for e in entries if e["kind"] == "spent")
    return {"earned": earned, "spent": spent, "profit": earned - spent}


def _lessons(tenant) -> str:
    done = Task.objects.filter(tenant=tenant, kind="money_plan", result__review__isnull=False)[:15]
    lines = []
    for p in done:
        r, t = p.result["review"], ledger_totals(p)
        lines.append(f"- {p.title}: {r.get('verdict')}, earned Rs {t['earned']}, spent Rs {t['spent']}. {r.get('lesson', '')}")
    return ("Results of past experiments (learn from them):\n" + "\n".join(lines) + "\n") if lines else ""


def _past(tenant) -> str:
    names = list(Task.objects.filter(tenant=tenant, kind="money_idea").values_list("title", flat=True)[:MAX_PAST])
    return ("Already proposed (do not repeat): " + "; ".join(names) + "\n") if names else ""


def _grounded(agent: AgentRuntime, system: str, user: str, task: Task | None = None):
    """Gemini with Google Search, so ideas come from what is selling now. None if unavailable."""
    if agent.dry_run or not _cfg(agent).get("web_research", True):
        return None
    model = agent.card.model.split("/")[-1]
    try:
        from google.genai import types

        from tools.genai_client import client

        agent.ensure_budget(0.05)
        resp = client(agent.api_key()).models.generate_content(
            model=model, contents=user,
            config=types.GenerateContentConfig(system_instruction=system,
                                               tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        usage = resp.usage_metadata
        tin, tout = getattr(usage, "prompt_token_count", 0) or 0, getattr(usage, "candidates_token_count", 0) or 0
        agent.log("llm_call", f"{model} researched the web: {tin + tout} tokens", task=task,
                  cost=token_cost(model, tin, tout), tokens=tin + tout)
        sources = []
        try:
            for chunk in resp.candidates[0].grounding_metadata.grounding_chunks or []:
                if chunk.web and chunk.web.uri:
                    sources.append({"title": chunk.web.title or chunk.web.uri, "url": chunk.web.uri})
        except (AttributeError, IndexError, TypeError):
            pass
        return parse_json(resp.text or ""), sources[:8]
    except Exception as exc:  # never lose a run: think without the web instead
        agent.log("earner_note", f"Web research unavailable ({str(exc)[:120]}); thinking without it", task=task)
        return None


def _sample_ideas(n: int) -> dict:
    """Dry run (no AI key): realistic ideas so the whole flow is testable for free."""
    base = [
        {"name": "Resume and LinkedIn makeovers for freshers", "field": "Careers",
         "how_it_makes_money": "Freshers pay Rs 499 per AI-polished resume + LinkedIn profile, sold via Instagram and Topmate",
         "why_now": "Campus placement season; thousands search for resume help every day.",
         "first_rupee_days": 5, "startup_cost_inr": 0, "monthly_low_inr": 5000, "monthly_high_inr": 40000,
         "owner_hours_per_week": 2, "ai_does": ["rewrites resumes", "writes posts", "answers enquiries"],
         "you_do": ["create a Topmate page", "approve each resume before it is sent"],
         "risks": "Needs trust; the first 5 customers are the hardest.", "score": 7},
        {"name": "Menu and poster design for local restaurants", "field": "Local business",
         "how_it_makes_money": "Restaurants pay Rs 1,500 for a festival menu + 5 social posters",
         "why_now": "Diwali season: every restaurant needs new offers and posters.",
         "first_rupee_days": 7, "startup_cost_inr": 300, "monthly_low_inr": 6000, "monthly_high_inr": 30000,
         "owner_hours_per_week": 3, "ai_does": ["designs posters", "writes WhatsApp pitches"],
         "you_do": ["visit or call 10 restaurants nearby"], "risks": "Price pressure from local printers.", "score": 6},
        {"name": "Printable planners on Etsy", "field": "Digital products",
         "how_it_makes_money": "Buyers worldwide pay $3-8 per printable planner download on Etsy",
         "why_now": "Planner searches peak before the new year.",
         "first_rupee_days": 14, "startup_cost_inr": 500, "monthly_low_inr": 2000, "monthly_high_inr": 25000,
         "owner_hours_per_week": 1, "ai_does": ["designs planners", "writes listings and tags"],
         "you_do": ["open an Etsy shop and link a bank account"], "risks": "Crowded market; needs a niche.", "score": 6},
    ]
    return {"ideas": (base * (n // len(base) + 1))[:n]}


def _sample_plan(idea: dict, days: int, budget: int) -> dict:
    return {"goal": f"Rs 2,000 earned from {idea.get('name', 'this idea')}", "goal_inr": 2000, "budget_inr": min(500, budget),
            "steps": [{"day": 1, "who": "you", "action": "Create the seller account and share the link here"},
                      {"day": 1, "who": "earner", "action": "Write the listing and the first 3 posts"},
                      {"day": 3, "who": "earner", "action": "Draft messages for 20 likely buyers"},
                      {"day": days, "who": "earner", "action": "Count money in and out, then decide scale or stop"}],
            "drafts": [{"title": "Listing", "text": f"{idea.get('name', 'Offer')}: fast, friendly, done for you."}],
            "stop_if": "No paying customer by day 10", "scale_if": "Two paying customers in the first week"}


# --- 1. hunt -------------------------------------------------------------------------------------

def hunt(tenant, n: int | None = None, note: str = "") -> list[Task]:
    """Propose new money ideas. Each lands on Today for a Try it / Change / Not for me."""
    agent = AgentRuntime(tenant, "earner")
    agent.ensure_active()
    cfg = _cfg(agent)
    n = int(n or cfg.get("ideas_per_hunt", 5))
    rules = agent.rules()
    system = HUNT_SYSTEM.format(
        tenant=tenant.name, about=cfg.get("about_owner", "a small business owner in India"),
        avoid=", ".join(cfg.get("avoid_fields") or []) or "none", first_days=30,
        budget=int(cfg.get("experiment_budget_inr", 2000)), hours=cfg.get("owner_hours_per_week", 5),
        rules=HOUSE_RULES, lessons=_lessons(tenant),
        learned=("The owner told you before (always follow):\n" + "\n".join(f"- {r}" for r in rules) + "\n") if rules else "",
        past=_past(tenant), note=(f"The owner asks this time: {note}\n" if note else ""), n=n)
    user = (f"Today is {timezone.localdate():%d %b %Y}. Search for what people are paying for right now "
            f"and give {n} money ideas.")
    grounded = _grounded(agent, system, user)
    if grounded:
        data, sources = grounded
    else:
        raw = agent.think([{"role": "system", "content": system}, {"role": "user", "content": user}],
                          json_mode=True, mock=json.dumps(_sample_ideas(n)))
        data, sources = parse_json(raw), []

    created = []
    for idea in (data.get("ideas") or [])[:n]:
        if not idea.get("name") or not idea.get("how_it_makes_money"):
            continue
        if word := is_blocked(idea):
            agent.log("earner_blocked", f"Dropped “{idea['name'][:80]}”: breaks the house rules ({word})")
            continue
        for k in ("first_rupee_days", "startup_cost_inr", "monthly_low_inr", "monthly_high_inr", "owner_hours_per_week", "score"):
            idea[k] = _int(idea.get(k))
        idea["sources"] = sources
        task = Task.objects.create(tenant=tenant, agent_key="earner", kind="money_idea", title=idea["name"][:200],
                                   status="awaiting_approval", payload={"note": note}, result=idea)
        Approval.objects.create(tenant=tenant, task=task)
        created.append(task)
    agent.log("earner_hunt", f"Earner found {len(created)} new money idea{'s' if len(created) != 1 else ''}"
                             + (" (with web research)" if grounded else ""))
    return created


# --- 2. plan -------------------------------------------------------------------------------------

def plan(idea_task: Task) -> Task:
    """You said "Try it": write the experiment and the first drafts."""
    agent = AgentRuntime(idea_task.tenant, "earner")
    cfg = _cfg(agent)
    days, cap = int(cfg.get("experiment_days", 14)), int(cfg.get("experiment_budget_inr", 2000))
    idea = idea_task.result or {}
    task = Task.objects.create(tenant=idea_task.tenant, agent_key="earner", kind="money_plan", parent=idea_task,
                               title=idea_task.title, status="running", payload={"idea_task": idea_task.id})
    try:
        raw = agent.think([
            {"role": "system", "content": PLAN_SYSTEM.format(tenant=idea_task.tenant.name, days=days, budget=cap,
                                                             about=cfg.get("about_owner", ""), rules=HOUSE_RULES)},
            {"role": "user", "content": "# The idea\n" + json.dumps(idea, ensure_ascii=False)
                                        + ("\n# The owner learned before\n" + "\n".join(agent.rules()) if agent.rules() else "")},
        ], task=task, json_mode=True, mock=json.dumps(_sample_plan(idea, days, cap)))
        p = parse_json(raw)
        now = timezone.now()
        p["budget_inr"] = min(_int(p.get("budget_inr"), cap), cap)
        p["goal_inr"] = _int(p.get("goal_inr"))
        p["steps"] = [s for s in p.get("steps") or [] if s.get("action")]
        p["drafts"] = [d for d in p.get("drafts") or [] if d.get("text")]
        task.result = {**p, "state": "running", "started": now.isoformat(timespec="minutes"),
                       "ends": (now + timedelta(days=days)).isoformat(timespec="minutes"), "ledger": []}
        task.status = "done"
        agent.log("earner_plan", f"Experiment started: {task.title} ({days} days, up to Rs {p['budget_inr']})", task=task)
    except Exception as exc:
        task.status, task.result = "failed", {"error": str(exc)[:300]}
        agent.log("task_failed", f"Earner could not plan “{task.title[:60]}”: {str(exc)[:150]}", task=task)
    task.save()
    return task


# --- 3. ledger -----------------------------------------------------------------------------------

def record(plan_task: Task, kind: str, amount_inr: int, note: str = "", user=None) -> dict:
    """You note money in or out. Returns the new totals."""
    if kind not in ("earned", "spent") or amount_inr <= 0:
        raise ValueError("kind must be earned or spent, amount above zero")
    result = dict(plan_task.result or {})
    result["ledger"] = [*(result.get("ledger") or []), {
        "kind": kind, "amount_inr": int(amount_inr), "note": note.strip()[:200],
        "at": timezone.now().isoformat(timespec="minutes"), "by": getattr(user, "username", "")}]
    plan_task.result = result
    plan_task.save(update_fields=["result", "updated"])
    totals = ledger_totals(plan_task)
    AgentRuntime(plan_task.tenant, "earner").log(
        f"money_{kind}", f"{plan_task.title}: Rs {amount_inr:,} {kind}" + (f" ({note.strip()[:80]})" if note.strip() else ""),
        task=plan_task, amount_inr=int(amount_inr))
    if kind == "spent" and totals["spent"] > _int(result.get("budget_inr")):
        AgentRuntime(plan_task.tenant, "earner").log(
            "earner_over_budget", f"{plan_task.title}: spent Rs {totals['spent']:,}, over the Rs {result.get('budget_inr')} cap",
            task=plan_task)
    return totals


# --- 4. review -----------------------------------------------------------------------------------

def review(plan_task: Task, force: bool = False) -> dict | None:
    """Scale, stop or extend an experiment that has run its course."""
    result = dict(plan_task.result or {})
    if result.get("state") != "running":
        return None
    ends = result.get("ends")
    if not force and ends and datetime.fromisoformat(ends) > timezone.now():
        return None
    agent = AgentRuntime(plan_task.tenant, "earner")
    totals = ledger_totals(plan_task)
    facts = {"idea": (plan_task.parent.result if plan_task.parent else {}), "goal": result.get("goal"),
             "goal_inr": result.get("goal_inr"), "budget_inr": result.get("budget_inr"), **totals,
             "ledger": result.get("ledger"), "stop_if": result.get("stop_if"), "scale_if": result.get("scale_if"),
             "already_extended": bool(result.get("extended"))}
    verdict_mock = {"verdict": "scale" if totals["profit"] > 0 else "stop",
                    "why": "It earned more than it cost." if totals["profit"] > 0 else "No paying customers yet.",
                    "lesson": "Ideas with a clear buyer and a small first price win faster.", "next_steps": []}
    raw = agent.think([
        {"role": "system", "content": REVIEW_SYSTEM.format(tenant=plan_task.tenant.name)},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ], task=plan_task, json_mode=True, mock=json.dumps(verdict_mock))
    r = parse_json(raw)
    verdict = r.get("verdict") if r.get("verdict") in ("scale", "stop", "extend") else "stop"
    if verdict == "extend" and result.get("extended"):
        verdict = "stop"  # one extension only; then decide
    r["verdict"] = verdict
    r["at"] = timezone.now().isoformat(timespec="minutes")
    if verdict == "extend":
        days = int(_cfg(agent).get("experiment_days", 14))
        result["ends"] = (timezone.now() + timedelta(days=days)).isoformat(timespec="minutes")
        result["extended"] = True
    else:
        result["state"] = "scaling" if verdict == "scale" else "stopped"
        result["review"] = r
    result["last_review"] = r
    plan_task.result = result
    plan_task.save(update_fields=["result", "updated"])
    agent.log("earner_review", f"{plan_task.title}: {verdict} (earned Rs {totals['earned']:,}, spent Rs {totals['spent']:,}). "
                               f"{r.get('why', '')}"[:300], task=plan_task)
    return r


def stop(plan_task: Task, reason: str = "") -> None:
    """You stopped it yourself."""
    result = dict(plan_task.result or {})
    result["state"] = "stopped"
    result["review"] = {"verdict": "stop", "why": reason or "Stopped by you.", "lesson": reason,
                        "at": timezone.now().isoformat(timespec="minutes")}
    plan_task.result = result
    plan_task.save(update_fields=["result", "updated"])
    AgentRuntime(plan_task.tenant, "earner").log("earner_review", f"{plan_task.title}: stopped by you. {reason}"[:300], task=plan_task)


def run_reviews(tenant) -> int:
    n = 0
    for p in Task.objects.filter(tenant=tenant, kind="money_plan", status="done", result__state="running"):
        try:
            if review(p):
                n += 1
        except Exception as exc:
            AgentRuntime(tenant, "earner").log("task_failed", f"Earner review of {p.title[:60]} failed: {str(exc)[:150]}", task=p)
    return n


def is_due(tenant, days=7) -> bool:
    from core.models import Event

    last = Event.objects.filter(tenant=tenant, agent_key="earner", kind="earner_hunt").order_by("-created").first()
    return last is None or last.created < timezone.now() - timedelta(days=days)
