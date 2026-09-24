"""Add your app / review the AI brand brief / per-app settings."""
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from PIL import Image

from agents.marketing import strategist
from agents.marketing.tasks import research_product
from core.jobs import enqueue
from core.models import Product, Task
from core.product_files import product_dir, write_product_files
from core.roles import can, require_role
from tools import playstore

from .forms import AppForm, BriefReviewForm, MarketingSettingsForm


def _home(slug=None):
    url = reverse("dashboard:home")
    return f"{url}?{urlencode({'p': slug})}" if slug else url


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def find_duplicate(tenant, name: str, store_url: str, exclude=None):
    """Returns (product, reason) if this app already exists, else (None, None)."""
    pid = playstore.package_id(store_url or "")
    new = _norm(name)
    for p in Product.objects.filter(tenant=tenant).exclude(status="archived"):
        if exclude is not None and p.id == exclude.id:
            continue
        cfg = p.config or {}
        links = [cfg.get("store_url", ""), (cfg.get("onboarding") or {}).get("store_url", "")]
        if pid and pid in {playstore.package_id(u) for u in links if u}:
            return p, "link"
        old = _norm(p.name)
        if new and old and (new == old or new.startswith(old) or old.startswith(new)):
            return p, "name"
    return None, None


def _save_logo(product, upload):
    folder = product_dir(product) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    img = Image.open(upload).convert("RGBA")
    img.thumbnail((512, 512))
    img.save(folder / "logo.png")


def _answers(form) -> dict:
    return {k: v for k, v in form.cleaned_data.items() if k not in ("logo", "confirm_new") and v}


def _start_research(request, product, form):
    answers = _answers(form)
    if form.cleaned_data.get("logo"):
        _save_logo(product, form.cleaned_data["logo"])
    cfg = dict(product.config or {})
    cfg["onboarding"] = answers
    if answers.get("store_url"):
        cfg["store_url"] = answers["store_url"]
    if answers.get("brand_colour"):
        cfg["accent"] = answers["brand_colour"]
    product.config = cfg
    product.save()
    enqueue(research_product, product.id, answers)
    messages.success(request, f"The agent is studying {product.name}: your answers, the store listing and the "
                              f"screenshots. The draft appears on this page in about a minute. Next: review it and click Activate.")
    return redirect("dashboard:product", slug=product.slug)


@login_required
def product_list(request):
    products = Product.objects.filter(tenant=request.tenant).order_by("name")
    return render(request, "dashboard/products.html", {
        "products": [p for p in products if p.status != "archived"],
        "archived": [p for p in products if p.status == "archived"],
    })


@login_required
@require_role("owner")
def product_new(request):
    form = AppForm(request.POST or None, request.FILES or None)
    duplicate, reason = None, None
    if request.method == "POST" and form.is_valid():
        duplicate, reason = find_duplicate(request.tenant, form.cleaned_data["name"], form.cleaned_data.get("store_url"))
        if duplicate is None or (reason == "name" and form.cleaned_data.get("confirm_new")):
            base = slugify(form.cleaned_data["name"])[:40] or "app"
            slug, n = base, 2
            while Product.objects.filter(tenant=request.tenant, slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            product = Product.objects.create(tenant=request.tenant, slug=slug, name=form.cleaned_data["name"],
                                             status="draft", config={"marketing": False, "shorts_per_day": 2})
            return _start_research(request, product, form)
    return render(request, "dashboard/product_form.html",
                  {"form": form, "is_new": True, "duplicate": duplicate, "dup_reason": reason})


@login_required
@require_role("owner")
def product_improve(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    initial = {"name": product.name, "store_url": (product.config or {}).get("store_url", ""),
               "brand_colour": (product.config or {}).get("accent") or (request.tenant.theme or {}).get("accent", "#C2410C")}
    initial.update((product.config or {}).get("onboarding") or {})
    form = AppForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        return _start_research(request, product, form)
    return render(request, "dashboard/product_form.html", {"form": form, "product": product, "is_new": False})


@login_required
def product_detail(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    cfg = product.config or {}
    draft = cfg.get("draft")
    source = draft or {"brief_markdown": product.brief, "pillars": cfg.get("pillars", []), "visual_style": cfg.get("visual_style", "")}
    form = BriefReviewForm(request.POST or None, initial={
        "brief": source.get("brief_markdown", ""),
        "pillars": strategist.pillars_to_text(source.get("pillars") or []),
        "visual_style": source.get("visual_style", ""),
    })
    if request.method == "POST" and not can(request, "owner"):
        messages.error(request, "Only owners can change the brand brief.")
        return redirect("dashboard:product", slug=product.slug)
    if request.method == "POST" and form.is_valid():
        was_live = product.status == "live" and not draft
        strategist.activate(product, request.user, form.cleaned_data["brief"], form.cleaned_data["pillars"],
                            form.cleaned_data["visual_style"])
        if was_live:
            messages.success(request, f"Saved. The agents use the updated brief for {product.name} from the next script.")
        else:
            messages.success(request, f"{product.name} is live. The Marketing agent now writes Shorts for it. "
                                      f"Next step: press “Write a Short now” to see the first one.")
        return redirect(_home(product.slug))
    running = Task.objects.filter(product=product, kind="product_brief", status="running").exists()
    last = Task.objects.filter(product=product, kind="product_brief").first()
    logo = product_dir(product) / "assets" / "logo.png"
    settings_form = MarketingSettingsForm(initial={"marketing": cfg.get("marketing", False),
                                                   "shorts_per_day": cfg.get("shorts_per_day", 2)})
    return render(request, "dashboard/product_detail.html", {
        "product": product, "draft": draft, "form": form, "running": running, "last": last,
        "has_logo": logo.exists(), "settings_form": settings_form,
        "screens": len(list((product_dir(product) / "assets" / "screens").glob("*.png"))),
    })


@login_required
@require_POST
@require_role("owner")
def product_settings(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    form = MarketingSettingsForm(request.POST)
    if form.is_valid():
        cfg = dict(product.config or {})
        want_on = form.cleaned_data["marketing"]
        if want_on and product.status != "live":
            messages.error(request, "Activate the brand brief first; the agent needs it before it can market this app.")
            return redirect("dashboard:product", slug=slug)
        cfg["marketing"] = want_on
        cfg["shorts_per_day"] = form.cleaned_data["shorts_per_day"]
        product.config = cfg
        product.save()
        if product.status == "live":
            write_product_files(product)
        state = f"on, {cfg['shorts_per_day']} Short(s) a day" if want_on else "off"
        messages.success(request, f"Marketing for {product.name} is {state}.")
    return redirect("dashboard:product", slug=slug)


@login_required
@require_POST
@require_role("owner")
def product_archive(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    cfg = dict(product.config or {})
    cfg["marketing"] = False
    cfg.pop("draft", None)
    product.config = cfg
    product.status = "archived"
    product.save()
    if product_dir(product).exists():
        write_product_files(product)
    Task.objects.filter(product=product, status="awaiting_approval").update(status="rejected")
    messages.success(request, f"{product.name} is archived. Nothing is deleted; restore it any time from Products.")
    if request.POST.get("next") == "home":
        return redirect(_home("all"))
    return redirect("dashboard:products")


@login_required
@require_POST
@require_role("owner")
def product_restore(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    product.status = "live" if product.brief else "draft"
    product.save()
    if product_dir(product).exists():
        write_product_files(product)
    messages.success(request, f"{product.name} is restored. Marketing is off until you switch it on.")
    return redirect("dashboard:product", slug=slug)


@login_required
@require_POST
@require_role("owner")
def product_discard_draft(request, slug):
    product = get_object_or_404(Product, tenant=request.tenant, slug=slug)
    cfg = dict(product.config or {})
    cfg.pop("draft", None)
    product.config = cfg
    product.save()
    Task.objects.filter(product=product, kind="product_brief", status="awaiting_approval").update(status="rejected")
    messages.info(request, "Draft discarded. The current brief stays active.")
    return redirect("dashboard:product", slug=product.slug)
