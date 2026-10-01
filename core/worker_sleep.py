"""On a small shared server the worker sleeps when there is no work, so the other app has the machine.

WORKER_SLEEPS=1 in .env turns it on. The worker then stays up only:
- during the daily work windows (WORK_WINDOWS, India time: the Scout, script and Analyst slots), and
- while a job is running or waiting, and for WORKER_IDLE_MINUTES after the last one.
Otherwise it stops itself. `deploy/worker-wake.sh` (cron, every minute) starts it again when a window
opens or a job is waiting in the queue (for example, you approved a script on Telegram).
The website and the Telegram chat are not affected: they run in the web container all day.
"""
import logging
import os
import signal
import threading
import time
from datetime import datetime, time as dtime

log = logging.getLogger("seyalini")

_state = {"busy": 0, "last": time.monotonic()}


def parse_windows(text: str) -> list[tuple[dtime, dtime]]:
    """"05:50-07:30,20:50-21:20" -> [(05:50, 07:30), (20:50, 21:20)]. Bad pieces are ignored."""
    out = []
    for piece in (text or "").split(","):
        try:
            start, end = (datetime.strptime(x.strip(), "%H:%M").time() for x in piece.split("-"))
            out.append((start, end))
        except ValueError:
            continue
    return out


def in_window(now: dtime, windows: list[tuple[dtime, dtime]]) -> bool:
    return any(start <= now < end for start, end in windows)


def should_stop(now: dtime, windows, busy: bool, idle_seconds: float, queued: int, idle_minutes: int) -> bool:
    """Sleep only when nothing runs, nothing waits, no window is open and it has been quiet for a while."""
    return not busy and queued == 0 and not in_window(now, windows) and idle_seconds >= idle_minutes * 60


def _queued(app) -> int:
    try:
        with app.connection_or_acquire() as conn:
            return int(conn.default_channel.client.llen("celery"))
    except Exception:  # cannot tell: assume there is work, so nothing is ever cut off
        return 1


def _watch(app, windows, idle_minutes: int):
    from django.utils import timezone

    while True:
        time.sleep(60)
        idle = time.monotonic() - _state["last"]
        if should_stop(timezone.localtime().time(), windows, _state["busy"] > 0, idle, _queued(app), idle_minutes):
            log.info("Worker: no work for %d minutes and no work window open. Sleeping until there is work.", idle_minutes)
            os.kill(os.getpid(), signal.SIGTERM)  # a clean stop: exit code 0, so Docker does not restart it
            return


def install(app):
    """Called from config/celery.py. Does nothing unless WORKER_SLEEPS is on."""
    from celery.signals import task_postrun, task_prerun, worker_ready

    @task_prerun.connect(weak=False)
    def _start(**_):
        _state["busy"] += 1

    @task_postrun.connect(weak=False)
    def _done(**_):
        _state["busy"] = max(0, _state["busy"] - 1)
        _state["last"] = time.monotonic()

    @worker_ready.connect(weak=False)
    def _ready(**_):
        from django.conf import settings

        if not getattr(settings, "WORKER_SLEEPS", False):
            return
        _state["last"] = time.monotonic()
        threading.Thread(target=_watch, name="worker-sleep", daemon=True,
                         args=(app, parse_windows(settings.WORK_WINDOWS), int(settings.WORKER_IDLE_MINUTES))).start()
