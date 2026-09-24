"""Who may do what inside an organisation.

owner    - everything: settings, API keys, team, apps, approvals
reviewer - approve / redo scripts and videos, press "Write a Short"
viewer   - read only
The platform admin (Django superuser = you) is owner everywhere.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

RANK = {"viewer": 1, "reviewer": 2, "owner": 3}
LABELS = {"owner": "Owner", "reviewer": "Reviewer", "viewer": "Viewer"}


def role_of(user, tenant) -> str:
    if not (user and user.is_authenticated and tenant):
        return ""
    if user.is_superuser:
        return "owner"
    m = tenant.memberships.filter(user=user).first()
    return m.role if m else ""


def can(request, role: str) -> bool:
    return RANK.get(getattr(request, "role", ""), 0) >= RANK[role]


def require_role(role: str):
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can(request, role):
                messages.error(request, f"You need the {LABELS[role]} role for that. Ask your organisation's owner.")
                return redirect("dashboard:home")
            return view(request, *args, **kwargs)
        return wrapped
    return deco
