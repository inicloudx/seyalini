"""Docker only: keep the tenants folder on the data volume.

The dashboard writes there (logos, store screenshots, briefs, app and organisation settings), so it
must survive every rebuild. The code's copy is used to fill it:
- first start: everything is copied;
- every start: agent cards (agents/*.yaml) are refreshed from the code, because git is their home;
  apps and organisation settings are never overwritten, because the dashboard is their home.
Does nothing when TENANTS_DIR is the code's own folder (laptop).
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Copy tenants/ from the code to TENANTS_DIR (data volume) without overwriting dashboard edits."

    def handle(self, *args, **opts):
        src, dst = Path(settings.BASE_DIR) / "tenants", Path(settings.TENANTS_DIR)
        if src.resolve() == dst.resolve() or not src.exists():
            return
        new = cards = 0
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            target = dst / f.relative_to(src)
            is_card = f.parent.name == "agents" and f.suffix == ".yaml"
            if target.exists() and not is_card:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, target)
            new, cards = new + (not is_card), cards + is_card
        self.stdout.write(f"tenants: {new} new file(s), {cards} agent card(s) refreshed")
