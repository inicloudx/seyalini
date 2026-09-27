from celery import shared_task

from core.models import Tenant

from . import scout


@shared_task
def run_scout(tenant_id: int | None = None):
    from agents.publisher.youtube_publisher import is_connected

    tenants = Tenant.objects.filter(id=tenant_id) if tenant_id else Tenant.objects.all()
    return {t.slug: scout.run(t) for t in tenants if is_connected(t)}
