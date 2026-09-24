from django.core.management.base import BaseCommand, CommandError

from core.models import Product


class Command(BaseCommand):
    help = "Run an agent now, e.g.: python manage.py run_agent marketing --product alphamagic"

    def add_arguments(self, parser):
        parser.add_argument("agent", choices=["marketing"])
        parser.add_argument("--tenant", default="inixr")
        parser.add_argument("--product", default="alphamagic")
        parser.add_argument("--slot", default="any")

    def handle(self, agent, tenant, product, slot, **opts):
        from agents.marketing.script_writer import write_script

        p = Product.objects.filter(tenant__slug=tenant, slug=product).first()
        if not p:
            raise CommandError(f"No product {tenant}/{product}. Run load_tenants first.")
        task = write_script(p, slot=slot)
        s = task.result
        self.stdout.write(self.style.SUCCESS(f"Task #{task.id} awaiting approval: {s['title']}"))
        self.stdout.write(f"Hook: {s['hook']}")
        for i, sc in enumerate(s["scenes"], 1):
            self.stdout.write(f"  {i}. [{sc.get('seconds')}s] {sc.get('on_screen_text')}\n     veo: {sc.get('veo_prompt')}")
        self.stdout.write(f"Caption: {s['caption']}\n{' '.join(s['hashtags'])}")
