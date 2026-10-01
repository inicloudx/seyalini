#!/usr/bin/env bash
# ONE-TIME preparation for Seyalini on the server that already runs Earnly (Docker is installed, ports are open).
# Adds swap for video rendering, gets the code from GitHub, installs the worker wake-up and the daily backup.
# Safe to run again. It stops with a clear message when it needs something from you.
#
#   copy to the server (repo is private), then:  bash server-setup.sh      (as ubuntu, NOT with sudo)
set -euo pipefail
trap 'echo; echo "STOPPED: a step failed (line $LINENO): $BASH_COMMAND"; echo "Send this message to Claude."' ERR

REPO=git@github.com:inicloudx/seyalini.git
DIR=$HOME/seyalini
say() { echo; echo "==> $*"; }
[ "$(id -u)" != 0 ] || { echo "Run as ubuntu without sudo:  bash server-setup.sh"; exit 1; }

say "1/4 Docker and Earnly"
command -v docker >/dev/null || { echo "Docker is missing. Set up Earnly first (its deploy/server-setup.sh)."; exit 1; }
docker network inspect earnly_default >/dev/null 2>&1 || { echo "Earnly is not running (no earnly_default network). Start it: cd ~/earnly && ./deploy.sh"; exit 1; }
echo "ok"

say "2/4 More swap (two apps on a 1 GB server: video rendering needs room)"
total_kb=$(awk '/SwapTotal/{print $2}' /proc/meminfo)
if [ "$total_kb" -lt 3500000 ] && ! swapon --show | grep -q /swapfile2; then
    sudo fallocate -l 3G /swapfile2 && sudo chmod 600 /swapfile2 && sudo mkswap /swapfile2 >/dev/null && sudo swapon /swapfile2
    grep -q '^/swapfile2' /etc/fstab || echo '/swapfile2 none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    echo "added 3 GB swap"
else
    echo "enough already"
fi
free -h | sed -n 3p

say "3/4 Code from GitHub"
if [ ! -d "$DIR/.git" ]; then
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    [ -f ~/.ssh/seyalini_deploy ] || ssh-keygen -q -t ed25519 -N "" -C "seyalini-server" -f ~/.ssh/seyalini_deploy
    grep -q "Host github-seyalini" ~/.ssh/config 2>/dev/null || cat >> ~/.ssh/config <<'EOF'
Host github-seyalini
    HostName github.com
    User git
    IdentityFile ~/.ssh/seyalini_deploy
    IdentitiesOnly yes
EOF
    chmod 600 ~/.ssh/config
    touch ~/.ssh/known_hosts
    grep -q "^github.com " ~/.ssh/known_hosts || ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null || true
    if ! git clone -q "${REPO/github.com/github-seyalini}" "$DIR"; then
        echo
        echo "STOP: GitHub needs this server's key. In GitHub -> inicloudx/seyalini -> Settings -> Deploy keys ->"
        echo "Add deploy key (title 'seyalini server', leave 'Allow write access' OFF), paste this line:"
        echo
        cat ~/.ssh/seyalini_deploy.pub
        echo
        echo "Then run:  bash server-setup.sh   again."
        exit 0
    fi
fi
echo "code is in $DIR"

say "4/4 Worker wake-up (every minute) and daily backup (03:15, keeps 14 days in ~/seyalini-backups)"
command -v crontab >/dev/null || { sudo apt-get install -y -qq cron >/dev/null && sudo systemctl enable --now cron >/dev/null; }
# (keep every other cron line, e.g. Earnly's backup; "crontab -l" fails when there is none yet)
{ { crontab -l 2>/dev/null || true; } | { grep -v "seyalini/deploy/" || true; }
  echo "* * * * * $DIR/deploy/worker-wake.sh >/dev/null 2>&1"
  echo "15 3 * * * $DIR/deploy/backup.sh >/dev/null 2>&1"; } | crontab -

echo
echo "Server ready. Next:"
echo "  cd ~/seyalini && ./deploy.sh"
