#!/usr/bin/env bash
# One-time setup on any laptop (new or old).
#   bash scripts/setup.sh            -> secrets + start everything + admin user
#   bash scripts/setup.sh --env-only -> only make sure .env exists
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. Secrets: prefer Bitwarden (free), fall back to the template
if [ ! -f .env ]; then
  if command -v bw >/dev/null 2>&1 && bw status 2>/dev/null | grep -q '"unlocked"'; then
    echo "-> Pulling .env from Bitwarden note 'seyalini-env'"
    bw get notes seyalini-env > .env
  else
    echo "-> No .env found. Creating one from .env.example (dry-run mode, costs nothing)."
    echo "   Tip: store your real .env in Bitwarden as 'seyalini-env' to skip this on the next laptop."
    cp .env.example .env
    SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(40))" 2>/dev/null || date +%s%N)
    sed -i.bak "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=${SECRET}|" .env && rm -f .env.bak
  fi
fi

[ "${1:-}" = "--env-only" ] && exit 0

# 2. Start the lab
docker compose up -d --build
echo "-> Waiting for the web container..."
sleep 8

# 3. Admin user (skip if it exists)
docker compose exec web python manage.py shell -c \
  "from django.contrib.auth import get_user_model as g; import sys; sys.exit(0 if g().objects.filter(is_superuser=True).exists() else 1)" \
  || docker compose exec web python manage.py createsuperuser

docker compose exec web python manage.py load_tenants
echo ""
echo "Ready: http://localhost:8000"
