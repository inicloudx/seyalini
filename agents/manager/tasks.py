from celery import shared_task

from core.models import Tenant


@shared_task
def telegram_update(tenant_id: int, update: dict):
    """One Telegram message or button tap (runs on the quick "chat" queue)."""
    from dashboard.chat_views import process_update

    process_update(Tenant.objects.get(id=tenant_id), update)
