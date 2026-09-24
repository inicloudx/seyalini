"""Works out which organisation (tenant) the logged-in user is working in."""
from .models import Tenant


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.role = ""
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            qs = Tenant.objects.all() if user.is_superuser else Tenant.objects.filter(memberships__user=user)
            slug = request.session.get("tenant")
            request.tenant = (slug and qs.filter(slug=slug).first()) or qs.order_by("id").first()
        from .roles import role_of

        request.role = role_of(user, request.tenant)
        return self.get_response(request)


def tenant_context(request):
    from .roles import RANK

    from django.conf import settings

    role = getattr(request, "role", "")
    return {"tenant": getattr(request, "tenant", None), "role": role, "allow_signup": settings.ALLOW_SIGNUP,
            "can_review": RANK.get(role, 0) >= 2, "is_owner": role == "owner"}
