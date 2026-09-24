from celery import shared_task

from core.models import Product, Task

from . import script_writer, video_producer


@shared_task
def daily_scripts(slot: str = "any"):
    return [t.id for t in script_writer.daily_run(slot)]


@shared_task
def write_script(product_id: int, slot: str = "any"):
    return script_writer.write_script(Product.objects.get(id=product_id), slot=slot).id


@shared_task
def rewrite_script(task_id: int):
    old = Task.objects.get(id=task_id)
    return script_writer.write_script(old.product, redo_of=old).id


@shared_task
def research_product(product_id: int, answers: dict):
    from . import strategist

    return strategist.research_product(Product.objects.get(id=product_id), answers).id


@shared_task
def make_video(script_task_id: int):
    return video_producer.produce_video(Task.objects.get(id=script_task_id)).id


@shared_task
def remake_video(video_task_id: int):
    old = Task.objects.get(id=video_task_id)
    script = Task.objects.get(id=old.payload["script_task"])
    return video_producer.produce_video(script, redo_of=old).id
