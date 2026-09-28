import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("seyalini")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["agents.marketing", "agents.publisher", "agents.analyst", "agents.scout", "agents.earner", "agents.worker"])

# The company clock (India time). Scripts are written early so you can approve
# them before the posting slots in Step 5 (9 AM and 6 PM).
app.conf.beat_schedule = {
    "scout-morning": {  # study today's top Shorts before the 7 AM script is written
        "task": "agents.scout.tasks.run_scout",
        "schedule": crontab(hour=6, minute=0),
    },
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
    "analyst-daily": {
        "task": "agents.analyst.tasks.run_analyst",
        "schedule": crontab(hour=21, minute=0),
    },
    "earner-weekly-hunt": {  # new money ideas once a week (checked daily, so a missed Monday is caught up)
        "task": "agents.earner.tasks.weekly_hunt",
        "schedule": crontab(hour=8, minute=0),
    },
    "earner-team": {  # the Earner's worker agents draft their daily / weekly work
        "task": "agents.worker.tasks.run_workers",
        "schedule": crontab(hour=9, minute=0),
    },
    "earner-reviews": {  # scale or stop experiments that have run their 14 days
        "task": "agents.earner.tasks.run_reviews",
        "schedule": crontab(hour=21, minute=30),
    },
}
