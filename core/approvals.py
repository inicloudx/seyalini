"""Your decisions. A redo reason becomes a permanent rule for that agent,
so the same mistake does not come back.

Flow: script approved -> video is produced -> video approved -> ready to publish (Step 5).
"""
from django.db import transaction
from django.utils import timezone

from .jobs import enqueue
from .models import Event, Rule, Task


@transaction.atomic
def decide(task: Task, user, decision: str, reason: str = "") -> Task:
    approval = task.approval
    if approval.decision != "pending":
        return task
    approval.decision = decision
    approval.reason = reason.strip()[:300]
    approval.decided_by = user
    approval.decided_at = timezone.now()
    approval.save()

    what = "video" if task.kind == "short_video" else "script"
    if decision == "approved":
        task.status = "approved"
        Event.objects.create(tenant=task.tenant, agent_key=task.agent_key, task=task, kind="approved",
                             message=f"You approved the {what}: {task.result.get('title', task.title)}")
    else:
        task.status = "rejected"
        if approval.reason:
            text = approval.reason if task.kind != "short_video" else f"Video: {approval.reason}"
            Rule.objects.create(tenant=task.tenant, agent_key=task.agent_key, product=task.product,
                                text=text, source="rejection")
        Event.objects.create(tenant=task.tenant, agent_key=task.agent_key, task=task, kind="redo",
                             message=f"Redo {what}: {approval.reason or 'no reason given'}")
    task.save()

    from agents.marketing import tasks as mt

    follow_up = {
        ("short_script", "approved"): mt.make_video,
        ("short_script", "redo"): mt.rewrite_script,
        ("short_video", "redo"): mt.remake_video,
    }.get((task.kind, decision))
    if follow_up is not None:
        transaction.on_commit(lambda: enqueue(follow_up, task.id))
    return task
