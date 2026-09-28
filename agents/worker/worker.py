"""Worker agents: the team the Earner builds. Every worker is just an agent card (config.kind = "worker")
with its own instructions, schedule and budget; this one module runs all of them.

On each run a worker reads its experiment (goal, plan, money so far), its past output and what you taught
it, then drafts today's work. The draft waits for your check: "Use it", "Change something" (it redoes it
and remembers), or "Reject". Workers draft only; they cannot spend, post, or create more agents.
"""
import json
from datetime import timedelta

from django.utils import timezone

from agents.earner.earner import HOUSE_RULES, is_blocked, ledger_totals
from agents.llm import parse_json
from agents.runtime import AgentRuntime, BudgetExceeded
from core.models import AgentCard, Approval, Task

EVERY = {"day": timedelta(hours=20), "week": timedelta(days=6, hours=20)}

WORK_SYSTEM = """You are {name}, an AI agent in {tenant}'s money-making team, created by the Earner.
Your job: {role}
Your standing instructions: {instructions}

You work for this experiment: {experiment}
Goal: {goal}. Money so far: earned Rs {earned}, spent Rs {spent}.
The plan: {steps}

{rules}
You draft only: the owner checks your work and does anything that needs an account, money or sending.
{learned}{recent}
Reply with ONLY a JSON object:
{{"title": "what you made today, a few words",
  "items": [{{"title": "what this piece is", "text": "the ready-to-use text"}}],
  "note_for_owner": "one line: what to do with it, or what you need"}}"""


def _sample(card: AgentCard) -> dict:
    return {"title": f"{card.name}: today's drafts",
            "items": [{"title": "Post 1", "text": "Ready in 24 hours, made for you. Message us to start."},
                      {"title": "Post 2", "text": "Three happy customers this week. Yours next?"}],
            "note_for_owner": "Post these on the shop page today."}


def run(tenant, key: str, redo_of: Task | None = None) -> Task | None:
    agent = AgentRuntime(tenant, key)
    card = agent.card
    cfg = card.config or {}
    if cfg.get("kind") != "worker":
        raise ValueError(f"{key} is not a worker agent")
    agent.ensure_active()
    plan = Task.objects.filter(id=cfg.get("plan_task")).first()
    pr = (plan.result if plan else {}) or {}
    totals = ledger_totals(plan) if plan else {"earned": 0, "spent": 0}
    recent = list(Task.objects.filter(tenant=tenant, agent_key=key, kind="work_output").select_related("approval")[:5])
    rules = agent.rules()
    system = WORK_SYSTEM.format(
        name=card.name, tenant=tenant.name, role=card.role, instructions=cfg.get("instructions", ""),
        experiment=plan.title if plan else "general", goal=pr.get("goal", "earn money"),
        earned=totals["earned"], spent=totals["spent"],
        steps="; ".join(f"day {s.get('day')}: {s.get('action')}" for s in (pr.get("steps") or [])[:8]) or "none",
        rules=HOUSE_RULES,
        learned=("The owner taught you (always follow):\n" + "\n".join(f"- {r}" for r in rules) + "\n") if rules else "",
        recent=("Your recent work (do not repeat it): " + "; ".join(
            f"{t.title} ({getattr(getattr(t, 'approval', None), 'decision', '?')})" for t in recent) + "\n") if recent else "")
    user = f"Today is {timezone.localdate():%d %b %Y}. Make today's work."
    if redo_of is not None:
        user += (f"\nRedo this, the owner said: {getattr(getattr(redo_of, 'approval', None), 'reason', '') or 'make it better'}\n"
                 + json.dumps(redo_of.result, ensure_ascii=False)[:4000])
    task = Task.objects.create(tenant=tenant, agent_key=key, kind="work_output", parent=redo_of, status="running",
                               title=f"{card.name}: working", payload={"plan_task": plan.id if plan else None})
    try:
        out = parse_json(agent.think([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                     task=task, json_mode=True, mock=json.dumps(_sample(card))))
        out["items"] = [i for i in out.get("items") or [] if i.get("text")]
        if word := is_blocked(out):
            raise ValueError(f"the draft broke the house rules ({word})")
        if not out["items"]:
            raise ValueError("the draft was empty")
        task.title = (out.get("title") or f"{card.name}: today's work")[:200]
        task.result, task.status = {**out, "agent_name": card.name, "experiment": plan.title if plan else ""}, "awaiting_approval"
        task.save()
        Approval.objects.create(tenant=tenant, task=task)
        agent.log("worker_output", f"{card.name} made: {task.title}", task=task)
    except BudgetExceeded:
        task.status, task.result = "failed", {"error": "Monthly budget used up."}
        task.save()
    except Exception as exc:
        task.status, task.result = "failed", {"error": str(exc)[:300]}
        task.save()
        agent.log("task_failed", f"{card.name} could not work today: {str(exc)[:150]}", task=task)
    return task


def is_due(card: AgentCard) -> bool:
    last = Task.objects.filter(tenant=card.tenant, agent_key=card.key, kind="work_output").exclude(status="failed").first()
    gap = EVERY.get((card.config or {}).get("every"), EVERY["day"])
    return last is None or last.created < timezone.now() - gap


def run_due(tenant) -> int:
    from agents.earner.team import workers

    n = 0
    for card in workers(tenant, active_only=True):
        if is_due(card):
            try:
                t = run(tenant, card.key)
                n += int(bool(t and t.status == "awaiting_approval"))
            except Exception:  # one worker failing never blocks the others
                pass
    return n
