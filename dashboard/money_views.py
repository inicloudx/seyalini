"""Money: the Earner's ideas, running experiments and the money ledger."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from agents.earner import earner, team
from core.jobs import enqueue
from core.models import AgentCard, Event, Task
from core.roles import require_role

from .views import REASON_CHIPS

IDEA_CHIPS = REASON_CHIPS["money_idea"]


def _earner(tenant):
    return AgentCard.objects.filter(tenant=tenant, key="earner", is_current=True).first()


def money_summary(tenant) -> dict:
    """Totals for the Today card and the Money page."""
    plans = list(Task.objects.filter(tenant=tenant, kind="money_plan", status="done"))
    earned = spent = 0
    for p in plans:
        t = earner.ledger_totals(p)
        earned, spent = earned + t["earned"], spent + t["spent"]
    return {"earned": earned, "spent": spent, "profit": earned - spent,
            "running": sum(1 for p in plans if (p.result or {}).get("state") in ("running", "scaling")),
            "ideas": Task.objects.filter(tenant=tenant, kind="money_idea", status="awaiting_approval").count(),
            "agents": len(team.workers(tenant, active_only=True))}


@login_required
def money(request):
    if request.tenant is None:
        return render(request, "dashboard/no_tenant.html")
    t = request.tenant
    ideas = list(Task.objects.filter(tenant=t, kind="money_idea", status="awaiting_approval"))
    for i in ideas:
        i.chips = IDEA_CHIPS
    team_items = list(Task.objects.filter(tenant=t, kind__in=["agent_proposal", "work_output"], status="awaiting_approval"))
    for i in team_items:
        i.chips = REASON_CHIPS[i.kind]
    crew = [c for c in team.workers(t) if c.status != "planned"]
    for c in crew:
        c.spent = c.spent_this_month()
        c.last = Task.objects.filter(tenant=t, agent_key=c.key, kind="work_output").first()
    plans = list(Task.objects.filter(tenant=t, kind="money_plan").exclude(status="failed").select_related("parent"))
    for p in plans:
        p.totals = earner.ledger_totals(p)
        p.state = (p.result or {}).get("state", "working" if p.status == "running" else "")
    order = {"scaling": 0, "running": 1, "working": 2, "stopped": 3}
    plans.sort(key=lambda p: (order.get(p.state, 9), -p.id))
    card = _earner(t)
    return render(request, "dashboard/money.html", {
        "ideas": ideas, "plans": plans, "team_items": team_items, "crew": crew, "summary": money_summary(t), "card": card,
        "cfg": (card.config if card else {}) or {},
        "events": Event.objects.filter(tenant=t, agent_key="earner").exclude(kind="llm_call").select_related("task")[:15],
    })


@login_required
@require_role("reviewer")
@require_POST
def money_hunt(request):
    from agents.earner.tasks import run_hunt

    card = _earner(request.tenant)
    if not card or card.status != "active":
        messages.error(request, "The Earner is not switched on for this organisation.")
    else:
        note = request.POST.get("note", "").strip()[:200]
        enqueue(run_hunt, request.tenant.id, None, note)
        messages.success(request, "The Earner is searching for new ways to earn. New ideas appear here in a minute or two.")
    return redirect("dashboard:money")


@login_required
@require_role("reviewer")
@require_POST
def money_record(request, task_id):
    plan = get_object_or_404(Task, id=task_id, tenant=request.tenant, kind="money_plan")
    kind = request.POST.get("kind")
    try:
        amount = int(float(request.POST.get("amount", "0").replace(",", "")))
        totals = earner.record(plan, kind, amount, request.POST.get("note", ""), request.user)
        word = "earned" if kind == "earned" else "spent"
        messages.success(request, f"Noted: ₹{amount:,} {word}. {plan.title} has made ₹{totals['profit']:,} profit so far.")
    except ValueError:
        messages.error(request, "Enter an amount above zero.")
    return redirect(f"{reverse('dashboard:money')}#plan-{plan.id}")


@login_required
@require_role("reviewer")
@require_POST
def money_stop(request, task_id):
    plan = get_object_or_404(Task, id=task_id, tenant=request.tenant, kind="money_plan")
    earner.stop(plan, request.POST.get("reason", "").strip()[:200])
    messages.success(request, f"Stopped {plan.title}. The Earner will remember why.")
    return redirect("dashboard:money")


@login_required
@require_role("reviewer")
@require_POST
def money_review(request, task_id):
    plan = get_object_or_404(Task, id=task_id, tenant=request.tenant, kind="money_plan")
    try:
        r = earner.review(plan, force=True)
        if r:
            messages.success(request, f"The Earner says: {r['verdict']}. {r.get('why', '')}")
    except Exception as exc:
        messages.error(request, f"Could not review now: {str(exc)[:200]}")
    return redirect("dashboard:money")


@login_required
@require_role("reviewer")
@require_POST
def agent_run(request, key):
    from agents.worker.tasks import run_worker

    card = get_object_or_404(AgentCard, tenant=request.tenant, key=key, is_current=True, config__kind="worker")
    if card.status != "active":
        messages.error(request, f"{card.name} is paused. Restart it first.")
    else:
        enqueue(run_worker, request.tenant.id, card.key)
        messages.success(request, f"{card.name} is working. Its draft appears here in a minute.")
    return redirect("dashboard:money")


@login_required
@require_role("owner")
@require_POST
def agent_toggle(request, key):
    card = get_object_or_404(AgentCard, tenant=request.tenant, key=key, is_current=True, config__kind="worker")
    new = team.set_status(card, "paused" if card.status == "active" else "active",
                          "Paused by you" if card.status == "active" else "Restarted by you")
    messages.success(request, f"{new.name} is {'working again' if new.status == 'active' else 'paused'}.")
    return redirect("dashboard:money")
