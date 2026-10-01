#!/usr/bin/env bash
# Start or update Seyalini on the server:  cd ~/seyalini && ./deploy.sh
# First time: makes .env with new secrets. Every time: pulls the latest code, rebuilds, restarts.
# Seyalini shares the server with Earnly, whose nginx does https for seyalini.inixr.com (see Earnly's deploy.sh).
set -euo pipefail
cd "$(dirname "$0")"

echo "▶ Latest code"
git pull -q --ff-only

if [ ! -f .env ]; then
    echo "▶ First start: making .env with new secrets"
    secret() { python3 -c 'import secrets;print(secrets.token_urlsafe(50))'; }
    cp deploy/env.production.example .env
    sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(secret)|; s|^SEYALINI_ENCRYPTION_KEY=.*|SEYALINI_ENCRYPTION_KEY=$(secret)|" .env
    chmod 600 .env
    echo "  Save DJANGO_SECRET_KEY and SEYALINI_ENCRYPTION_KEY from ~/seyalini/.env in your password manager."
fi

if ! docker network inspect earnly_default >/dev/null 2>&1; then
    echo "STOP: Earnly's Docker network (earnly_default) is missing. Start Earnly first: cd ~/earnly && ./deploy.sh"
    exit 1
fi

echo "▶ Building (the first time takes several minutes on this small server)"
docker compose build -q
echo "▶ Starting"
docker compose up -d

echo
docker compose ps -a --format "table {{.Name}}\t{{.Status}}"
echo
echo "✅ Seyalini is up. The worker sleeps when there is no work and wakes by itself."
if ! grep -q '^GEMINI_API_KEY=.\+' .env; then
    echo "   Next: put your Gemini key in ~/seyalini/.env (GEMINI_API_KEY=...), then run ./deploy.sh again."
fi
echo "   First time only, your login:  docker compose exec web python manage.py createsuperuser --username nithy"
