"""Organisation settings (owner), platform admin (you), optional self sign-up."""
from pathlib import Path

import yaml
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core.models import Membership, Product, TenantSecret, Tenant
from core.orgs import add_member, create_organisation
from core.roles import LABELS, require_role
from core.secrets import KNOWN, get_secret, own_secret, set_secret, uses_platform_keys


# --- forms -------------------------------------------------------------------
class OrgForm(forms.Form):
    name = forms.CharField(label="Organisation name", max_length=120)
    owner_name = forms.CharField(label="Your name (used in greetings)", max_length=60, required=False)
    accent = forms.CharField(label="Brand colour", widget=forms.TextInput(attrs={"type": "color"}))
    logo_text = forms.CharField(label="Logo letters (1–3)", max_length=3)


class MemberForm(forms.Form):
    username = forms.CharField(max_length=60)
    email = forms.EmailField(required=False)
    role = forms.ChoiceField(choices=[("reviewer", "Reviewer: approves content"), ("viewer", "Viewer: read only"),
                                      ("owner", "Owner: full control")])
    password = forms.CharField(label="Temporary password (for new people)", required=False,
                               widget=forms.PasswordInput(render_value=False))


class NewOrgForm(forms.Form):
    name = forms.CharField(label="Organisation name", max_length=120)
    owner_username = forms.CharField(label="Owner username", max_length=60)
    owner_email = forms.EmailField(label="Owner email", required=False)
    password = forms.CharField(label="Owner password", widget=forms.PasswordInput)
    use_platform_keys = forms.BooleanField(label="Let this organisation use MY API keys (you pay)", required=False)

    def clean_password(self):
        pw = self.cleaned_data["password"]
        try:
            validate_password(pw)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages)
        return pw


# --- helpers -----------------------------------------------------------------------
def _write_tenant_file(tenant):
    """INIXR-style organisations live in tenants/<slug>/tenant.yaml: keep that file in sync."""
    path = Path(settings.TENANTS_DIR) / tenant.slug / "tenant.yaml"
    if not path.exists():
        return
    data = {"slug": tenant.slug, "name": tenant.name, "theme": tenant.theme, "settings": tenant.settings}
    path.write_text("# Organisation settings. Edited from the dashboard Settings page.\n"
                    + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _test_gemini(key: str) -> tuple[bool, str]:
    try:
        from google import genai

        text = genai.Client(api_key=key).models.generate_content(model="gemini-2.5-flash", contents="Reply with the word OK").text
        return True, f"The key works (the model answered “{(text or '').strip()[:20]}”)."
    except Exception as exc:
        return False, f"The key did not work: {str(exc)[:200]}"


# --- organisation settings ------------------------------------------------------------
@login_required
@require_role("owner")
def settings_home(request):
    t = request.tenant
    theme, sett = dict(t.theme or {}), dict(t.settings or {})
    org_form = OrgForm(initial={"name": t.name, "owner_name": sett.get("owner_name", ""),
                                "accent": theme.get("accent", "#C2410C"), "logo_text": theme.get("logo_text", "S")})
    member_form = MemberForm()
    if request.method == "POST":
        section = request.POST.get("section")
        if section == "org":
            org_form = OrgForm(request.POST)
            if org_form.is_valid():
                d = org_form.cleaned_data
                t.name = d["name"]
                theme.update({"accent": d["accent"], "logo_text": d["logo_text"].upper()})
                sett["owner_name"] = d["owner_name"]
                t.theme, t.settings = theme, sett
                t.save()
                _write_tenant_file(t)
                messages.success(request, "Organisation details saved.")
                return redirect("dashboard:settings")
        elif section == "keys":
            changed = []
            for name in KNOWN:
                val = request.POST.get(name, "").strip()
                if request.POST.get(f"remove_{name}"):
                    set_secret(t, name, "")
                    changed.append(f"removed {name}")
                elif val:
                    set_secret(t, name, val)
                    changed.append(f"saved {name}")
            msg = "; ".join(changed) or "Nothing changed. Leave a field empty to keep the saved key."
            if any(c == "saved GEMINI_API_KEY" for c in changed):
                ok, result = _test_gemini(own_secret(t, "GEMINI_API_KEY"))
                msg += ". " + result
                (messages.success if ok else messages.error)(request, msg)
            else:
                messages.success(request, msg)
            return redirect("dashboard:settings")
        elif section == "test":
            key = get_secret(t, "GEMINI_API_KEY")
            if not key:
                messages.error(request, "No Gemini key yet. Add one below.")
            else:
                ok, result = _test_gemini(key)
                (messages.success if ok else messages.error)(request, result)
            return redirect("dashboard:settings")
        elif section == "member_add":
            member_form = MemberForm(request.POST)
            if member_form.is_valid():
                d = member_form.cleaned_data
                exists = get_user_model().objects.filter(username__iexact=d["username"]).exists()
                if not exists:
                    try:
                        validate_password(d["password"] or "")
                    except ValidationError as exc:
                        member_form.add_error("password", exc.messages)
                if not member_form.errors:
                    user, created = add_member(t, d["username"], d["email"], d["password"], d["role"])
                    messages.success(request, f"{user.username} added as {LABELS[d['role']]}."
                                              + (" Send them the username and temporary password; they can change it after login." if created else ""))
                    return redirect("dashboard:settings")
        elif section == "member_remove":
            m = Membership.objects.filter(tenant=t, id=request.POST.get("membership")).select_related("user").first()
            owners = Membership.objects.filter(tenant=t, role="owner").count()
            if m and m.role == "owner" and owners <= 1:
                messages.error(request, "An organisation needs at least one owner.")
            elif m:
                m.delete()
                messages.success(request, f"{m.user.username} no longer has access to {t.name}.")
            return redirect("dashboard:settings")
        elif section == "member_role":
            m = Membership.objects.filter(tenant=t, id=request.POST.get("membership")).first()
            role = request.POST.get("role")
            owners = Membership.objects.filter(tenant=t, role="owner").count()
            if m and role in LABELS and not (m.role == "owner" and role != "owner" and owners <= 1):
                m.role = role
                m.save()
                messages.success(request, "Role updated.")
            else:
                messages.error(request, "An organisation needs at least one owner.")
            return redirect("dashboard:settings")

    saved = {s.name: s for s in TenantSecret.objects.filter(tenant=t)}
    keys = [{"name": n, "label": label, "saved": saved.get(n), "platform": uses_platform_keys(t) and not saved.get(n) and bool(get_secret(t, n))}
            for n, label in KNOWN.items()]
    return render(request, "dashboard/settings.html", {
        "org_form": org_form, "member_form": member_form, "keys": keys,
        "members": Membership.objects.filter(tenant=t).select_related("user").order_by("user__username"),
        "roles": LABELS, "platform_keys": uses_platform_keys(t),
    })


# --- platform admin (only you) -----------------------------------------------------------
def _is_admin(u):
    return u.is_authenticated and u.is_superuser


@login_required
@user_passes_test(_is_admin)
def platform(request):
    form = NewOrgForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tenant, user = create_organisation(d["name"], d["owner_username"], d["owner_email"], d["password"],
                                               use_platform_keys=d["use_platform_keys"])
        except ValueError as exc:
            form.add_error("owner_username", str(exc))
        else:
            messages.success(request, f"{tenant.name} is ready. Give {user.username} the login; they add their own "
                                      f"Gemini key in Settings{' (or use yours)' if d['use_platform_keys'] else ''}.")
            return redirect("dashboard:platform")
    orgs = []
    for t in Tenant.objects.order_by("name"):
        orgs.append({"t": t, "members": t.memberships.count(), "products": Product.objects.filter(tenant=t).exclude(status="archived").count(),
                     "has_key": bool(own_secret(t, "GEMINI_API_KEY")), "platform": uses_platform_keys(t)})
    return render(request, "dashboard/platform.html", {"form": form, "orgs": orgs})


@login_required
@user_passes_test(_is_admin)
@require_POST
def platform_open(request, slug):
    request.session["tenant"] = slug
    request.session["focus"] = "all"
    return redirect("dashboard:home")


# --- optional public sign-up ------------------------------------------------------------------
class SignupForm(NewOrgForm):
    use_platform_keys = None


def signup(request):
    if not settings.ALLOW_SIGNUP:
        raise Http404
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            tenant, user = create_organisation(d["name"], d["owner_username"], d["owner_email"], d["password"])
        except ValueError as exc:
            form.add_error("owner_username", str(exc))
        else:
            login(request, user)
            request.session["tenant"] = tenant.slug
            messages.success(request, f"Welcome! {tenant.name} is ready. First, add your Gemini key in Settings, then add your app.")
            return redirect("dashboard:home")
    return render(request, "registration/signup.html", {"form": form})
