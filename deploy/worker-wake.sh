#!/usr/bin/env bash
# Wakes Seyalini's worker when there is work (cron, every minute; server-setup.sh installs it).
# The worker puts itself to sleep (core/worker_sleep.py); this starts it again when
#   - a job is waiting in the queue (you approved a script or video, or asked for a new Short), or
#   - a daily work window is open (WORK_WINDOWS in .env, India time).
# Cheap on purpose: two tiny docker calls, no Python.
set -u
cd "$(dirname "$0")/.."

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]; }
running seyalini_worker && exit 0          # already awake
running seyalini_web || exit 0             # Seyalini is switched off: leave it off
running seyalini_redis || exit 0

queued=$(docker exec seyalini_redis redis-cli llen celery 2>/dev/null | tr -dc '0-9')
windows=$(grep -E '^WORK_WINDOWS=' .env 2>/dev/null | cut -d= -f2)
windows=${windows:-05:50-07:30,13:50-14:30,20:50-21:20}
now=$(TZ=Asia/Kolkata date +%H:%M)

wake=0
[ "${queued:-0}" -gt 0 ] && wake=1
IFS=','
for w in $windows; do
    start=${w%-*}; end=${w#*-}
    if [[ ! "$now" < "$start" && "$now" < "$end" ]]; then wake=1; fi
done

[ "$wake" = 1 ] && docker start seyalini_worker >/dev/null
exit 0
