from django.core.management.base import BaseCommand, CommandError

from core.models import Task


class Command(BaseCommand):
    help = "Make the video for a script task now, e.g.: python manage.py make_video 12"

    def add_arguments(self, parser):
        parser.add_argument("script_task_id", type=int)

    def handle(self, script_task_id, **opts):
        from agents.marketing.video_producer import produce_video

        script = Task.objects.filter(id=script_task_id, kind="short_script").first()
        if not script:
            raise CommandError(f"No script task #{script_task_id}")
        task = produce_video(script)
        self.stdout.write(self.style.SUCCESS(f"Video task #{task.id}: media/{task.result['video']} ({task.result['seconds']:.0f}s)"))
