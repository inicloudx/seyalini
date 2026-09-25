import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("seyalini")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["agents.marketing", "agents.publisher"])

# The company clock (India time). Scripts are written early so you can approve
# them before the posting slots in Step 5 (9 AM and 6 PM).
app.conf.beat_schedule = {
    "marketing-morning-script": {
        "task": "agents.marketing.tasks.daily_scripts",
        "schedule": crontab(hour=7, minute=0),
        "kwargs": {"slot": "morning"},
    },
    "marketing-evening-script": {
        "task": "agents.marketing.tasks.daily_scripts",
        "schedule": crontab(hour=14, minute=0),
        "kwargs": {"slot": "evening"},
    },
}
