"""The Earner builds its own team. When an experiment needs repeated work (daily posts, new listings,
replies to buyers...), or a test earns and should grow, the Earner proposes a new worker agent.
You approve it like anything else; then it becomes a real agent card with its own budget, and it
works on a schedule, bringing its output to you for a check.

Limits (in earner.yaml): how many workers may be active, the most each may cost a month, and the most
the whole team may cost. Workers draft only; they cannot spend, post, or create more agents.
When an experiment stops, its workers are paused with it.
"""
from decimal import Decimal

from django.utils.text import slugify

from agents.runtime import AgentRuntime
from core.models import AgentCard, Approval, Task

MAX_AGENTS = 5
MAX_AGENT_BUDGET_USD = 2
MAX_TEAM_BUDGET_USD = 10


def _limits(earner: AgentRuntime) -> tuple[int, Decimal, Decimal]:
    cfg = earner.card.config or {}
    return (int(cfg.get("max_agents", MAX_AGENTS)), Decimal(str(cfg.get("max_agent_budget_usd", MAX_AGENT_BUDGET_USD))),
            Decimal(str(cfg.get("max_team_budget_usd", MAX_TEAM_BUDGET_USD))))


def workers(tenant, plan_task: Task | None = None, active_only=False) -> list[AgentCard]:
    qs = AgentCard.objects.filter(tenant=tenant, is_current=True, config__kind="worker")
    if plan_task is not None:
        qs = qs.filter(config__plan_task=plan_task.id)
    if active_only:
        qs = qs.filter(status="active")
    return list(qs.order_by("key"))


def _clean(spec: dict, cap: Decimal) -> dict | None:
    name, instructions = (spec.get("name") or "").strip()[:60], (spec.get("instructions") or "").strip()
    if not name or not instructions:
        return None
    try:
        budget = Decimal(str(spec.get("monthly_budget_usd") or 1))
    except Exception:
        budget = Decimal("1")
    return {"name": name, "role": (spec.get("role") or "").strip()[:200], "instructions": instructions[:1500],
            "every": "week" if spec.get("every") == "week" else "day",
            "monthly_budget_usd": str(min(max(budget, Decimal("0.2")), cap))}


def propose(plan_task: Task, specs: list[dict], why: str = "") -> list[Task]:
    """New worker agents the experiment needs. Each waits for your yes."""
    from .earner import is_blocked

    earner = AgentRuntime(plan_task.tenant, "earner")
    max_n, cap, _ = _limits(earner)
    open_n = len(workers(plan_task.tenant, active_only=True)) + Task.objects.filter(
        tenant=plan_task.tenant, kind="agent_proposal", status="awaiting_approval").count()
    taken = {c.name.lower() for c in workers(plan_task.tenant, plan_task)}
    out = []
    for spec in specs or []:
        s = _clean(spec, cap)
        if s is None or s["name"].lower() in taken:
            continue
        if word := is_blocked(s):
            earner.log("earner_blocked", f"Dropped new agent “{s['name']}”: breaks the house rules ({word})", task=plan_task)
            continue
        if open_n >= max_n:
            earner.log("earner_note", f"Wanted a new agent “{s['name']}” but the team is full ({max_n}). "
                                      f"Raise max_agents in earner.yaml or pause one.", task=plan_task)
            break
        task = Task.objects.create(tenant=plan_task.tenant, agent_key="earner", kind="agent_proposal", parent=plan_task,
                                   title=f"New agent: {s['name']}"[:200], status="awaiting_approval",
                                   payload={"plan_task": plan_task.id}, result={**s, "why": why, "experiment": plan_task.title})
        Approval.objects.create(tenant=plan_task.tenant, task=task)
        taken.add(s["name"].lower())
        open_n += 1
        out.append(task)
        from . import chat

        chat.notify(plan_task.tenant, f"🤖 Can I create a new agent?\n\n{s['name']}: {s['role']}\nWhy: {why}\n"
                                      f"For: {plan_task.title}\nIt works every {s['every']}, costs up to ${s['monthly_budget_usd']}/month, "
                                      f"and only drafts for your check.",
                    chat.yes_no(task, "✓ Yes, create it", "✕ No"))
    if out:
        earner.log("earner_team", f"{plan_task.title}: proposed {len(out)} new agent{'s' if len(out) != 1 else ''} "
                                  f"({', '.join(t.result['name'] for t in out)})", task=plan_task)
    return out


def _new_key(tenant, name: str) -> str:
    base = "w-" + (slugify(name)[:40].strip("-") or "agent")
    key, i = base, 2
    while AgentCard.objects.filter(tenant=tenant, key=key).exists():
        key, i = f"{base}-{i}", i + 1
    return key


def spawn(proposal: Task) -> AgentCard | None:
    """You said yes: the agent card is created and starts working on its schedule."""
    earner = AgentRuntime(proposal.tenant, "earner")
    max_n, cap, team_cap = _limits(earner)
    s = proposal.result or {}
    active = workers(proposal.tenant, active_only=True)
    budget = min(Decimal(s.get("monthly_budget_usd", "1")), cap)
    team_budget = sum((c.monthly_budget_usd for c in active), Decimal("0"))
    problem = ""
    if len(active) >= max_n:
        problem = f"the team is full ({max_n} agents)"
    elif team_budget + budget > team_cap:
        problem = f"the team would cost more than ${team_cap} a month"
    plan = Task.objects.filter(id=(proposal.payload or {}).get("plan_task")).first()
    if not problem and plan and (plan.result or {}).get("state") == "stopped":
        problem = "its experiment has stopped"
    if problem:
        proposal.status = "failed"
        proposal.result = {**s, "error": f"Not created: {problem}."}
        proposal.save(update_fields=["status", "result", "updated"])
        earner.log("earner_note", f"Did not create “{s.get('name')}”: {problem}", task=proposal)
        return None
    card = AgentCard.objects.create(
        tenant=proposal.tenant, key=_new_key(proposal.tenant, s["name"]), version=1, name=s["name"], role=s.get("role", ""),
        status="active", autonomy=1, model=earner.card.model, monthly_budget_usd=budget,
        changelog=f"Created by the Earner for “{s.get('experiment', '')}”"[:300],
        config={"kind": "worker", "made_by": "earner", "plan_task": plan.id if plan else None,
                "instructions": s["instructions"], "every": s.get("every", "day")})
    proposal.status = "done"
    proposal.result = {**s, "agent_key": card.key}
    proposal.save(update_fields=["status", "result", "updated"])
    earner.log("earner_team", f"New agent at work: {card.name} (every {s.get('every', 'day')}, up to ${budget}/month)", task=proposal)
    return card


def set_status(card: AgentCard, status: str, why: str = "") -> AgentCard:
    """Cards are never edited: a pause or restart is a new version, the old one is kept."""
    if card.status == status:
        return card
    card.is_current = False
    card.save(update_fields=["is_current"])
    new = AgentCard.objects.create(
        tenant=card.tenant, key=card.key, version=card.version + 1, name=card.name, role=card.role, status=status,
        autonomy=card.autonomy, model=card.model, monthly_budget_usd=card.monthly_budget_usd, config=card.config,
        changelog=(why or f"Set to {status}")[:300])
    AgentRuntime(card.tenant, "earner").log("earner_team", f"{card.name}: {'restarted' if status == 'active' else status}. {why}"[:300])
    return new


def pause_for_plan(plan_task: Task, why: str) -> int:
    n = 0
    for card in workers(plan_task.tenant, plan_task, active_only=True):
        set_status(card, "paused", why)
        n += 1
    return n
