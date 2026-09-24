from django.core.management.base import BaseCommand

from core.loader import load_all


class Command(BaseCommand):
    help = "Load tenants, products (brand briefs) and agent cards from the tenants/ folder."

    def handle(self, *args, **opts):
        for line in load_all():
            self.stdout.write(line)
