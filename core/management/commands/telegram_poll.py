"""Laptop mode for Telegram chat: no public web address, so ask Telegram for new messages instead.

    python manage.py telegram_poll      (keep it running in a second window; Ctrl+C to stop)

On the server Telegram calls Seyalini directly (webhook), so this is not needed there.
"""
import time

from django.core.management.base import BaseCommand

from core.models import Tenant
from core.secrets import get_secret


class Command(BaseCommand):
    help = "Receive Telegram chat messages on a laptop (long polling)."

    def handle(self, *args, **opts):
        from dashboard.chat_views import process_update
        from tools import telegram

        bots = {t: get_secret(t, "TELEGRAM_BOT_TOKEN") for t in Tenant.objects.all()}
        bots = {t: tok for t, tok in bots.items() if tok}
        if not bots:
            self.stderr.write("No organisation has a Telegram bot token. Add it in Settings first.")
            return
        for tenant, token in bots.items():
            telegram.call(token, "deleteWebhook")  # polling and a webhook cannot both be on
            self.stdout.write(f"Listening to Telegram for {tenant.name}…")
        offsets = {t.id: 0 for t in bots}
        while True:
            for tenant, token in bots.items():
                try:
                    updates = telegram.call(token, "getUpdates", {"offset": offsets[tenant.id], "timeout": 20,
                                                                  "allowed_updates": ["message", "callback_query"]}, timeout=30)
                except Exception as exc:
                    self.stderr.write(f"{tenant.name}: {exc}")
                    time.sleep(5)
                    continue
                for u in updates:
                    offsets[tenant.id] = u["update_id"] + 1
                    tenant.refresh_from_db()
                    try:
                        process_update(tenant, u)
                    except Exception as exc:
                        self.stderr.write(f"{tenant.name}: could not handle a message: {exc}")
