"""What every agent gets for free: its card, a budget guard, the event log,
its learned rules and one call to the AI model. New agents reuse this."""
import logging

from core.models import AgentCard, Event, Rule, Task, Tenant

from . import llm

log = logging.getLogger("seyalini")


class BudgetExceeded(Exception):
    pass


class AgentNotActive(Exception):
    pass


class AgentRuntime:
    def __init__(self, tenant: Tenant, key: str):
        self.tenant = tenant
        self.key = key
        self.card = AgentCard.objects.get(tenant=tenant, key=key, is_current=True)

    # --- whose key / free mode ---------------------------------------------
    @property
    def dry_run(self) -> bool:
        from core.secrets import is_dry_run

        return is_dry_run(self.tenant)

    def api_key(self, name: str | None = None) -> str:
        from core.secrets import get_secret, key_for_model

        return get_secret(self.tenant, name) if name else key_for_model(self.tenant, self.card.model)

    # --- guard rails -------------------------------------------------------
    def ensure_active(self):
        if self.card.status != "active":
            raise AgentNotActive(f"{self.card.name} is {self.card.status}")

    def ensure_budget(self, estimate_usd=0):
        from decimal import Decimal

        left = self.card.budget_left()
        if left <= 0 or left < Decimal(str(estimate_usd)):
            self.log("budget_blocked", f"{self.card.name}: ${left:.2f} left this month, job needs ~${float(estimate_usd):.2f}. "
                                       f"Raise monthly_budget_usd or wait for next month.")
            raise BudgetExceeded(self.card.name)

    def spend(self, kind: str, message: str, cost_usd, task: Task | None = None, **data) -> Event:
        """Record a non-text AI cost (images, video)."""
        from decimal import Decimal

        return self.log(kind, message, task=task, cost=Decimal(str(cost_usd)), **data)

    # --- memory ------------------------------------------------------------
    def rules(self, product=None) -> list[str]:
        qs = Rule.objects.filter(tenant=self.tenant, agent_key=self.key, active=True)
        if product is not None:
            qs = qs.filter(product__isnull=True) | qs.filter(product=product)
        return [r.text for r in qs.order_by("created")]

    # --- logging -----------------------------------------------------------
    def log(self, kind: str, message: str, task: Task | None = None, cost=0, tokens=0, **data) -> Event:
        log.info("[%s/%s] %s", self.tenant.slug, self.key, message)
        return Event.objects.create(tenant=self.tenant, agent_key=self.key, task=task, kind=kind,
                                    message=message[:300], cost_usd=cost, tokens=tokens, data=data)

    # --- thinking ----------------------------------------------------------
    def think(self, messages: list[dict], *, task: Task | None = None, json_mode=False, mock=None) -> str:
        self.ensure_budget()
        cfg = self.card.config or {}
        res = llm.complete(self.card.model, messages, temperature=float(cfg.get("temperature", 0.7)),
                           json_mode=json_mode, mock=mock, api_key=self.api_key(), dry_run=self.dry_run)
        tag = " (dry run)" if res.dry_run else ""
        self.log("llm_call", f"{res.model}: {res.tokens} tokens{tag}", task=task, cost=res.cost_usd,
                 tokens=res.tokens, dry_run=res.dry_run)
        return res.text
