#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server for Seyalini at https://seyalini.inixr.com
# (Oracle Cloud Always Free: A1 Ampere or E2 Micro). Safe to run again: it skips what is done
# and stops with a clear message whenever it needs something from you.
#
#   curl -fsSLO https://raw.githubusercontent.com/...   (repo is private, so instead:)
#   copy this file to the server, then:  sudo bash setup.sh
set -euo pipefail

DOMAIN=seyalini.inixr.com
REPO=git@github.com:inicloudx/seyalini.git
HOME_DIR=/opt/seyalini
APP=$HOME_DIR/app
VENV=$HOME_DIR/venv
say() { echo; echo "==> $*"; }
as_app() { sudo -u seyalini -H "$@"; }

[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo bash setup.sh"; exit 1; }

say "1/9 Packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-venv python3-pip git redis-server sqlite3 ffmpeg nginx certbot python3-certbot-nginx \
    iptables-persistent >/dev/null
systemctl enable --now redis-server >/dev/null

say "2/9 Swap (only on small servers)"
if [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 4000000 ] && ! swapon --show | grep -q /swapfile; then
    fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "added 4 GB swap"
else
    echo "not needed or already there"
fi

say "3/9 Firewall: open 80 and 443 (Oracle's Ubuntu image blocks everything except SSH)"
for port in 80 443; do
    iptables -C INPUT -m state --state NEW -p tcp --dport $port -j ACCEPT 2>/dev/null && continue
    reject=$(iptables -L INPUT --line-numbers -n | awk '$2=="REJECT"{print $1; exit}')
    if [ -n "$reject" ]; then  # must sit above Oracle's catch-all REJECT rule
        iptables -I INPUT "$reject" -m state --state NEW -p tcp --dport $port -j ACCEPT
    else
        iptables -A INPUT -m state --state NEW -p tcp --dport $port -j ACCEPT
    fi
done
netfilter-persistent save >/dev/null 2>&1 || true
echo "also open 80 and 443 in Oracle Console -> the VCN's Security List (Ingress, source 0.0.0.0/0)"

say "4/9 User and folders"
id seyalini >/dev/null 2>&1 || useradd -r -m -d $HOME_DIR -s /bin/bash seyalini
install -d -o seyalini -g seyalini $HOME_DIR/data $HOME_DIR/data/media $HOME_DIR/backups
# nginx (www-data) must be able to read the videos
chmod 755 $HOME_DIR $HOME_DIR/data $HOME_DIR/data/media

say "5/9 Code"
if [ ! -d $APP/.git ]; then
    if [ ! -f $HOME_DIR/.ssh/id_ed25519 ]; then
        as_app mkdir -p $HOME_DIR/.ssh
        as_app ssh-keygen -q -t ed25519 -N "" -C "seyalini-server" -f $HOME_DIR/.ssh/id_ed25519
    fi
    as_app sh -c "ssh-keyscan -q github.com >> $HOME_DIR/.ssh/known_hosts 2>/dev/null"
    if ! as_app git clone -q $REPO $APP; then
        echo
        echo "STOP: GitHub needs this server's key. In GitHub -> inicloudx/seyalini -> Settings ->"
        echo "Deploy keys -> Add deploy key (title 'server', leave 'write access' OFF), paste:"
        echo
        cat $HOME_DIR/.ssh/id_ed25519.pub
        echo
        echo "Then run: sudo bash setup.sh   again."
        exit 0
    fi
fi
[ -d $VENV ] || as_app python3 -m venv $VENV
as_app $VENV/bin/pip install -q --upgrade pip
as_app $VENV/bin/pip install -q -r $APP/requirements.txt

say "6/9 Settings (.env)"
if [ ! -f $APP/.env ]; then
    as_app cp $APP/deploy/env.production.example $APP/.env
    secret() { python3 -c 'import secrets;print(secrets.token_urlsafe(50))'; }
    sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$(secret)|; s|^SEYALINI_ENCRYPTION_KEY=.*|SEYALINI_ENCRYPTION_KEY=$(secret)|" $APP/.env
    chmod 600 $APP/.env && chown seyalini:seyalini $APP/.env
fi
if ! grep -q '^GEMINI_API_KEY=.\+' $APP/.env; then
    echo
    echo "STOP: add your Gemini key:  sudo nano $APP/.env   (line GEMINI_API_KEY=)"
    echo "Also save DJANGO_SECRET_KEY and SEYALINI_ENCRYPTION_KEY from that file in your password manager."
    echo "Then run: sudo bash setup.sh   again."
    exit 0
fi

say "7/9 Database"
as_app bash -c "cd $APP && set -a && source .env && set +a && \
    $VENV/bin/python manage.py migrate --noinput -v0 && \
    $VENV/bin/python manage.py load_tenants && \
    $VENV/bin/python manage.py collectstatic --noinput -v0"

say "8/9 Services, restart rights, daily backup"
cp $APP/deploy/seyalini-{web,worker,beat}.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q seyalini-web seyalini-worker seyalini-beat
systemctl restart seyalini-web seyalini-worker seyalini-beat
echo "seyalini ALL=(root) NOPASSWD: /usr/bin/systemctl restart seyalini-web seyalini-worker seyalini-beat" > /tmp/seyalini.sudo
visudo -cqf /tmp/seyalini.sudo && install -m 440 /tmp/seyalini.sudo /etc/sudoers.d/seyalini
rm -f /tmp/seyalini.sudo
( crontab -u seyalini -l 2>/dev/null | grep -v deploy/backup.sh; echo "30 2 * * * $APP/deploy/backup.sh" ) | crontab -u seyalini -

say "9/9 nginx"
# only the first time: certbot later adds the https part to this file
[ -f /etc/nginx/sites-available/seyalini ] || cp $APP/deploy/nginx-seyalini.conf /etc/nginx/sites-available/seyalini
ln -sf /etc/nginx/sites-available/seyalini /etc/nginx/sites-enabled/seyalini
rm -f /etc/nginx/sites-enabled/default
nginx -t -q && systemctl reload nginx

echo
echo "Done. Seyalini is running on http://$DOMAIN"
echo "Last steps (once the DNS record for $DOMAIN points here):"
echo "  sudo certbot --nginx -d $DOMAIN -m inicloudx@gmail.com --agree-tos --redirect -n"
echo "  sudo -u seyalini -H bash -c 'cd $APP && set -a && source .env && set +a && $VENV/bin/python manage.py createsuperuser --username nithy'"
