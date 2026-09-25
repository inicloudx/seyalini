"""The permanent foundation (Layer 4). Every table carries a tenant, so INIXR is
tenant 1 and any other organisation is simply another row."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Tenant(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    theme = models.JSONField(default=dict, blank=True)      # white-label: colours, logo
    settings = models.JSONField(default=dict, blank=True)   # timezone, channels, etc.
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    ROLES = [("owner", "Owner"), ("reviewer", "Reviewer"), ("viewer", "Viewer")]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLES, default="owner")

    class Meta:
        unique_together = ("user", "tenant")


class Product(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="products")
    slug = models.SlugField()
    name = models.CharField(max_length=120)
    status = models.CharField(max_length=30, default="live")
    brief = models.TextField(blank=True)                    # the brand brief every agent reads
    config = models.JSONField(default=dict, blank=True)     # pillars, platforms, rules

    class Meta:
        unique_together = ("tenant", "slug")

    def __str__(self):
        return self.name


class AgentCard(models.Model):
    """The agent registry. A change never edits a card: it adds a new version."""

    STATUS = [("active", "Active"), ("planned", "Planned"), ("paused", "Paused")]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="agents")
    key = models.SlugField()
    version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    name = models.CharField(max_length=80)
    role = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="planned")
    autonomy = models.PositiveSmallIntegerField(default=1)  # L0..L4
    model = models.CharField(max_length=100, blank=True)
    monthly_budget_usd = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("5.00"))
    config = models.JSONField(default=dict, blank=True)
    changelog = models.CharField(max_length=300, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "key", "version")
        ordering = ["key", "-version"]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def spent_this_month(self):
        start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = Event.objects.filter(tenant=self.tenant, agent_key=self.key, created__gte=start).aggregate(s=Sum("cost_usd"))["s"]
        return total or Decimal("0")

    def budget_left(self):
        return self.monthly_budget_usd - self.spent_this_month()


class Task(models.Model):
    """The task board: the only way work moves between agents and you."""

    STATUS = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("awaiting_approval", "Awaiting approval"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="tasks")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    agent_key = models.SlugField()
    kind = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    status = models.CharField(max_length=30, choices=STATUS, default="queued")
    payload = models.JSONField(default=dict, blank=True)    # input (the plan)
    result = models.JSONField(default=dict, blank=True)     # output (the script)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.title


class Approval(models.Model):
    DECISIONS = [("pending", "Pending"), ("approved", "Approved"), ("redo", "Redo"), ("discarded", "Rejected")]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="approvals")
    task = models.OneToOneField(Task, on_delete=models.CASCADE, related_name="approval")
    decision = models.CharField(max_length=20, choices=DECISIONS, default="pending")
    reason = models.CharField(max_length=300, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)


class Rule(models.Model):
    """Company memory: lessons that every future run of an agent must follow."""

    SOURCES = [("rejection", "Your rejection"), ("manual", "Written by you"), ("catalyst", "Catalyst")]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="rules")
    agent_key = models.SlugField()
    product = models.ForeignKey(Product, on_delete=models.CASCADE, null=True, blank=True)
    text = models.CharField(max_length=300)
    source = models.CharField(max_length=20, choices=SOURCES, default="manual")
    active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.text


class Event(models.Model):
    """The event log: every action and every rupee. Catalyst learns from this."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="events")
    agent_key = models.SlugField(blank=True)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    kind = models.CharField(max_length=50)
    message = models.CharField(max_length=300)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=Decimal("0"))
    tokens = models.PositiveIntegerField(default=0)
    data = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]


class TenantSecret(models.Model):
    """An organisation's own API keys (Gemini, Telegram...). Stored encrypted; never shown in full."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="secrets")
    name = models.CharField(max_length=60)          # e.g. GEMINI_API_KEY
    value_encrypted = models.TextField()
    hint = models.CharField(max_length=20, blank=True)  # e.g. "AIza…x9Qk" for display
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("tenant", "name")
