from celery import shared_task

from core.models import Task

from . import youtube_publisher


@shared_task
def publish_video(video_task_id: int):
    t = youtube_publisher.publish(Task.objects.get(id=video_task_id))
    return t.id if t else None
