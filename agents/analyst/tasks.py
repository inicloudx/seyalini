from celery import shared_task

from core.models import Tenant

from . import analyst


@shared_task
def run_analyst(tenant_id: int | None = None):
    tenants = Tenant.objects.filter(id=tenant_id) if tenant_id else Tenant.objects.all()
    from agents.publisher.youtube_publisher import is_connected

    return {t.slug: analyst.run(t) for t in tenants if is_connected(t)}
