from celery import shared_task

from core.models import Tenant

from . import analyst


@shared_task
def run_analyst(tenant_id: int | None = None):
    tenants = Tenant.objects.filter(id=tenant_id) if tenant_id else Tenant.objects.all()
    from agents.publisher.youtube_publisher import is_connected

    return {t.slug: analyst.run(t) for t in tenants if is_connected(t)}


@shared_task
def diagnose_views(product_id: int, note: str = ""):
    """"Why so few views?": asked from the Videos page or the Manager chat."""
    from core.models import Product

    from .diagnose import diagnose

    return diagnose(Product.objects.get(id=product_id), note=note).id


@shared_task
def apply_view_fixes(task_id: int):
    from core.models import Task

    from .diagnose import apply_fixes

    return len(apply_fixes(Task.objects.get(id=task_id)))


@shared_task
def rediagnose(task_id: int):
    """"Change" on a views check: look again with your note."""
    from core.models import Task

    from .diagnose import diagnose

    old = Task.objects.get(id=task_id)
    return diagnose(old.product, note=getattr(getattr(old, "approval", None), "reason", ""), redo_of=old).id
