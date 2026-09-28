#!/usr/bin/env bash
# Run on the server after `git push` from a laptop:  sudo -u seyalini /opt/seyalini/app/deploy/update.sh
set -euo pipefail
cd /opt/seyalini/app
git pull --ff-only
/opt/seyalini/venv/bin/pip install -q -r requirements.txt
set -a; source .env; set +a
/opt/seyalini/venv/bin/python manage.py migrate --noinput
/opt/seyalini/venv/bin/python manage.py load_tenants
/opt/seyalini/venv/bin/python manage.py collectstatic --noinput -v0
sudo systemctl restart seyalini-web seyalini-worker seyalini-chat seyalini-beat
echo "Seyalini updated."
