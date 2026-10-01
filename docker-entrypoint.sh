#!/bin/sh
# Runs first in both containers. Only the web container prepares the database, so the two never race.
set -e

mkdir -p /app/data/media /app/data/tenants /app/staticfiles
chown appuser:appgroup /app/data /app/data/media /app/data/tenants /app/staticfiles

if [ "$1" = "gunicorn" ]; then
    chown -R appuser:appgroup /app/data/tenants /app/staticfiles
    gosu appuser python manage.py seed_tenants
    gosu appuser python manage.py migrate --noinput -v0
    gosu appuser python manage.py load_tenants
    gosu appuser python manage.py collectstatic --noinput --clear -v0
fi

exec gosu appuser "$@"
