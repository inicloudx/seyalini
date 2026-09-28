from celery import shared_task

from core.models import Task, Tenant

from . import earner


def _active(tenant) -> bool:
    from core.models import AgentCard

    return AgentCard.objects.filter(tenant=tenant, key="earner", is_current=True, status="active").exists()


@shared_task
def run_hunt(tenant_id: int | None = None, n: int | None = None, note: str = ""):
    tenants = Tenant.objects.filter(id=tenant_id) if tenant_id else Tenant.objects.all()
    return {t.slug: len(earner.hunt(t, n=n, note=note)) for t in tenants if _active(t)}


@shared_task
def weekly_hunt():
    """Beat runs this daily; each organisation hunts at most once a week."""
    return {t.slug: len(earner.hunt(t)) for t in Tenant.objects.all() if _active(t) and earner.is_due(t)}


@shared_task
def make_plan(idea_task_id: int):
    return earner.plan(Task.objects.get(id=idea_task_id)).id


@shared_task
def rehunt(idea_task_id: int):
    """"Change something" on an idea: one fresh idea that follows your note."""
    old = Task.objects.get(id=idea_task_id)
    note = getattr(getattr(old, "approval", None), "reason", "")
    return [t.id for t in earner.hunt(old.tenant, n=1, note=note)]


@shared_task
def run_reviews(tenant_id: int | None = None):
    tenants = Tenant.objects.filter(id=tenant_id) if tenant_id else Tenant.objects.all()
    return {t.slug: earner.run_reviews(t) for t in tenants if _active(t)}
