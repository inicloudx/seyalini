from django.contrib import admin

from .models import AgentCard, Approval, Event, Membership, Product, Rule, Task, Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "created")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "status")
    list_filter = ("tenant",)


@admin.register(AgentCard)
class AgentCardAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "version", "is_current", "status", "autonomy", "model", "monthly_budget_usd")
    list_filter = ("tenant", "is_current", "status")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "tenant", "agent_key", "kind", "status", "created")
    list_filter = ("tenant", "status", "agent_key")


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("text", "tenant", "agent_key", "source", "active")
    list_filter = ("tenant", "source", "active")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("created", "tenant", "agent_key", "kind", "message", "cost_usd")
    list_filter = ("tenant", "agent_key", "kind")


admin.site.register(Membership)
admin.site.register(Approval)
