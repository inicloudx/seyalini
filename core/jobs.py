"""Run a job in the background, whatever machine you are on.

JOBS_MODE:
  celery - send to the Celery worker (Docker / server setup)
  thread - run in a background thread inside the web process (laptop without Redis)
  sync   - run immediately (tests)
"""
import logging
import threading

from django.conf import settings
from django.db import close_old_connections

log = logging.getLogger("seyalini")


def enqueue(task, *args):
    mode = getattr(settings, "JOBS_MODE", "celery")
    if mode == "celery":
        return task.delay(*args)
    if mode == "sync":
        return task.run(*args)

    def _run():
        try:
            task.run(*args)
        except Exception as exc:  # already logged as an Event by the agent
            log.warning("Background job %s failed: %s", task.name, exc)
        finally:
            close_old_connections()

    threading.Thread(target=_run, daemon=True, name=task.name).start()
