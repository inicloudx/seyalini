from celery import shared_task

from core.models import Task, Tenant

from . import worker


@shared_task
def run_workers():
    """Beat runs this daily; each worker works when its day or week has come round."""
    return {t.slug: worker.run_due(t) for t in Tenant.objects.all()}


@shared_task
def run_worker(tenant_id: int, key: str):
    t = worker.run(Tenant.objects.get(id=tenant_id), key)
    return t.id if t else None


@shared_task
def redo_output(task_id: int):
    old = Task.objects.get(id=task_id)
    return worker.run(old.tenant, old.agent_key, redo_of=old).id
