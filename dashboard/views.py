import shutil
from datetime import timedelta
from pathlib import Path
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.approvals import decide as decide_task
from core.roles import require_role
from core.secrets import is_dry_run
from core.jobs import enqueue
from core.models import AgentCard, Approval, Event, Product, Rule, Task, Tenant

REASON_CHIPS = {
    "short_script": ["Weak hook", "Too long", "Off-brand", "Too similar to a recent one", "Not kid-friendly", "Wrong facts"],
    "short_video": ["Too dark", "Text hard to read", "Visuals off-brand", "Boring, needs more motion", "Character looks wrong", "Not kid-friendly"],
}


def _need_tenant(request):
    if request.tenant is None:
        return render(request, "dashboard/no_tenant.html")
    return None


def _next_steps(t, products, dry_run, request=None):
    """The setup checklist: what the user should do next, one clear action at a time."""
    from core.product_files import product_dir

    live = [p for p in products if p.status == "live"]
    drafts = [p for p in products if (p.config or {}).get("draft")]
    marketed = [p for p in live if (p.config or {}).get("marketing")]
    approved = Task.objects.filter(tenant=t, status__in=["approved", "done"])
    first = drafts[0] if drafts else (products[0] if products else None)
    steps = [
        {"title": "Add your app", "done": bool(products), "why": "The agents need to know what they are marketing.",
         "action": "Add your app", "url": reverse("dashboard:product_new")},
        {"title": "Review and activate the brand brief", "done": bool(live) and not drafts,
         "why": "The agent read your answers and the store page. Check its brief, then click Activate.",
         "action": f"Review brief{f' for {first.name}' if first else ''}", "url": reverse("dashboard:product", args=[first.slug]) if first else ""},
        {"title": "Switch marketing on", "done": bool(marketed),
         "why": "Choose which apps get Shorts and how many per day.",
         "action": "Open marketing settings", "url": reverse("dashboard:product", args=[live[0].slug]) if live else ""},
        {"title": "Write and approve your first script", "done": approved.filter(kind="short_script").exists(),
         "why": "Press the Write button, read the script, then approve it or ask for a redo.", "action": "Write a Short now", "url": "#write"},
        {"title": "Approve your first video", "done": approved.filter(kind="short_video").exists(),
         "why": "After you approve a script, the video is made automatically. Watch it and approve it.", "action": "See the inbox", "url": "#inbox"},
        {"title": "Go live with real AI", "done": not dry_run,
         "why": "Add your organisation's Gemini API key in Settings. It is stored encrypted and your usage is billed to your own Google account. Until then, everything is free sample output.",
         "action": "Add your Gemini key", "url": reverse("dashboard:settings") + "#ai"},
        {"title": "Add your logo and music", "done": all((product_dir(p) / "assets" / "logo.png").exists() for p in marketed) if marketed else False,
         "why": "Logo and music make every video look like your brand. Upload the logo in “Improve with AI”; put music files in the app's assets/music folder.",
         "action": "Add logo", "url": reverse("dashboard:product_improve", args=[marketed[0].slug]) if marketed else ""},
    ]
    # A half-finished copy of an app that already exists? Offer to archive it instead of reviewing it.
    if first is not None and first.status != "live":
        from .product_views import find_duplicate

        cfg = first.config or {}
        dup, _ = find_duplicate(t, first.name, cfg.get("store_url") or (cfg.get("onboarding") or {}).get("store_url", ""), exclude=first)
        if dup is not None:
            steps[1].update({
                "title": f"Tidy up: “{first.name}” looks like a second copy of “{dup.name}”",
                "why": f"You started this app twice. “{dup.name}” is already set up, so archive this unfinished copy "
                       f"(nothing is deleted). If it really is a different app, review its brief instead.",
                "action": "Archive this copy", "post_url": reverse("dashboard:product_archive", args=[first.slug]),
                "alt_action": "Review it instead", "alt_url": reverse("dashboard:product", args=[first.slug]),
            })
    nxt = next((s for s in steps if not s["done"] and s["url"]), None)
    # Two LIVE apps that are really the same app (same store link / same name)? Tidy that up first.
    from .product_views import find_duplicate

    def richness(p):  # keep the most complete copy: real letter scenes, logo, longer brief, then the older one
        cfg = p.config or {}
        return (len(cfg.get("letter_words") or {}), (product_dir(p) / "assets" / "logo.png").exists(),
                len(p.brief or ""), -p.id)

    for p in live:
        cfg = p.config or {}
        dup, _ = find_duplicate(t, p.name, cfg.get("store_url") or (cfg.get("onboarding") or {}).get("store_url", ""), exclude=p)
        if dup is None or dup.status != "live":
            continue
        keep, copy = (p, dup) if richness(p) >= richness(dup) else (dup, p)
        nxt = {"title": f"Tidy up: “{copy.name}” is a second copy of “{keep.name}”",
               "why": f"Both are live, so the agents would make Shorts for the same app twice. Keep “{keep.name}” "
                      f"(the more complete one) and archive this copy. Nothing is deleted; you can restore it from Products.",
               "action": f"Archive “{copy.name}”", "post_url": reverse("dashboard:product_archive", args=[copy.slug]),
               "alt_action": "Compare them first", "alt_url": reverse("dashboard:products"), "url": "#"}
        break
    return steps, nxt, sum(s["done"] for s in steps)

STEPS = ["Idea", "Script", "Video", "Your check", "Ready"]
INR_PER_USD = 84  # rough, for display only


def _latest(task):
    """Follow redo / video children to the newest task of one Short."""
    seen = 0
    while seen < 10:
        child = Task.objects.filter(parent=task, kind__in=["short_script", "short_video"]).order_by("-id").first()
        if child is None:
            return task
        task, seen = child, seen + 1
    return task


def _flow(root):
    """One Short's journey as 5 steps: done / working / you / failed / todo."""
    cur = _latest(root)
    state = ["todo"] * 5
    note, task_for_you = "", None
    k, st = cur.kind, cur.status
    if k == "short_script":
        state[0] = "done"
        if st == "running":
            state[1], note = "working", "The AI is writing the script…"
        elif st == "awaiting_approval":
            state[1], state[3], note, task_for_you = "done", "you", "Script ready: check it and approve.", cur
        elif st == "failed":
            state[1], note = "failed", f"Script failed: {str(cur.result.get('error', ''))[:120]}"
        elif st == "rejected":
            state[1], note = "working", "Rewriting with your feedback…"
        else:  # approved, video not started yet
            state[1], state[2], note = "done", "working", "Starting the video…"
    else:
        state[0] = state[1] = "done"
        if st == "running":
            state[2], note = "working", "The AI is making the video (1–2 min)…"
        elif st == "awaiting_approval":
            state[2], state[3], note, task_for_you = "done", "you", "Video ready: watch it and approve.", cur
        elif st == "failed":
            state[2], note = "failed", f"Video failed: {str(cur.result.get('error', ''))[:120]}"
        elif st == "rejected":
            state[2], note = "working", "Remaking the video with your feedback…"
        else:
            state = ["done"] * 5
            note = "Ready to post."
    title = cur.result.get("title") or root.result.get("title") or root.title
    return {"id": root.id, "title": str(title).replace("[Sample] ", ""), "product": root.product,
            "steps": list(zip(STEPS, state)), "note": note, "task": task_for_you, "latest": cur,
            "done": state[-1] == "done", "failed": "failed" in state, "created": root.created}


@login_required
def home(request):
    if (resp := _need_tenant(request)) is not None:
        return resp
    t = request.tenant
    now = timezone.now()
    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago, month_ago = now - timedelta(days=7), now - timedelta(days=30)

    # --- which app are we looking at? ("All apps" or one) -----------------------
    products = list(Product.objects.filter(tenant=t).exclude(status="archived").order_by("name"))
    if "p" in request.GET:
        request.session["focus"] = request.GET["p"]
    focus = next((p for p in products if p.slug == request.session.get("focus")), None)
    tasks = Task.objects.filter(tenant=t).exclude(product__status="archived")
    events = Event.objects.filter(tenant=t)
    if focus:
        tasks = tasks.filter(product=focus)
        events = events.filter(task__product=focus)
    shown = [focus] if focus else products
    writable = [p for p in shown if p.status == "live" and (p.config or {}).get("marketing")]
    target = sum(int((p.config or {}).get("shorts_per_day", 2)) * 7 for p in writable)

    order = {"active": 0, "paused": 1, "planned": 2}
    agents = sorted(AgentCard.objects.filter(tenant=t, is_current=True), key=lambda a: (order.get(a.status, 9), a.key))
    for a in agents:
        a.spent = a.spent_this_month()
        a.pct = int(min(100, (a.spent / a.monthly_budget_usd * 100) if a.monthly_budget_usd else 0))
        a.last_event = Event.objects.filter(tenant=t, agent_key=a.key).first()

    decided = Approval.objects.filter(task__in=tasks, decided_at__gte=month_ago).exclude(decision="pending")
    n_dec = decided.count()
    approval_rate = round(100 * decided.filter(decision="approved").count() / n_dec) if n_dec else None
    spend = Event.objects.filter(tenant=t, created__gte=month_start).aggregate(s=Sum("cost_usd"))["s"] or Decimal("0")
    budget = sum((a.monthly_budget_usd for a in agents if a.status == "active"), Decimal("0"))

    pending = list(tasks.filter(status="awaiting_approval").select_related("product"))
    for task in pending:
        task.chips = REASON_CHIPS.get(task.kind, REASON_CHIPS["short_script"])
    counts = {}
    for task in Task.objects.filter(tenant=t, status="awaiting_approval").exclude(product__status="archived"):
        counts[task.product_id] = counts.get(task.product_id, 0) + 1
    for p in products:
        p.pending_count = counts.get(p.id, 0)
    dry = is_dry_run(t)
    steps, next_step, done = _next_steps(t, products, dry, request)
    roots = tasks.filter(kind="short_script", created__gte=now - timedelta(days=3)).exclude(parent__kind="short_script")[:6]
    flows = [_flow(r) for r in roots if not (r.result or {}).get("hidden")]
    active_flows = [f for f in flows if not f["done"]]
    week_videos = tasks.filter(kind="short_video", status="approved", updated__gte=week_ago).count()
    ctx = {
        "agents": agents,
        "focus": focus,
        "products": products,
        "writable": writable,
        "all_pending": sum(counts.values()),
        "steps": steps, "next_step": next_step, "steps_done": done,
        "pending": sorted(pending, key=lambda x: (x.kind != "short_video", -x.id)),  # videos first
        "running": Task.objects.filter(tenant=t, status="running").select_related("product"),
        "ready": tasks.filter(kind="short_video", status="approved")[:6],
        "recent": tasks.filter(kind__in=["short_script", "short_video"]).exclude(status__in=["awaiting_approval", "running"])[:10],
        "rules": Rule.objects.filter(tenant=t, active=True, **({"product": focus} if focus else {}))[:10],
        "events": events.select_related("task")[:25],
        "kpi": {
            "scripts_week": tasks.filter(kind="short_script", created__gte=week_ago).exclude(status="failed").count(),
            "target": target,
            "approval_rate": approval_rate,
            "spend": spend,
            "budget": budget,
            "spend_pct": int(min(100, spend / budget * 100)) if budget else 0,
        },
        "tenants": Tenant.objects.all() if request.user.is_superuser else Tenant.objects.filter(memberships__user=request.user),
        "dry_run": dry,
        "flows": active_flows[:4],
        "finished_flows": [f for f in flows if f["done"]][:3],
        "week_videos": week_videos,
        "spend_inr": int(spend * INR_PER_USD),
        "budget_inr": int(budget * INR_PER_USD),
        "video_mode": ((next((a for a in agents if a.key == "marketing"), None) or AgentCard()).config or {}).get("video", {}).get("mode", "images"),
    }
    return render(request, "dashboard/home.html", ctx)


@login_required
@require_POST
@require_role("reviewer")
def decide(request, task_id):
    task = get_object_or_404(Task, id=task_id, tenant=request.tenant)
    decision = request.POST.get("decision")
    if decision not in ("approved", "redo", "discarded"):
        return HttpResponseBadRequest("bad decision")
    reason = request.POST.get("reason") or request.POST.get("chip") or ""
    decide_task(task, request.user, decision, reason)
    task.refresh_from_db()
    if request.headers.get("HX-Request"):
        return render(request, "dashboard/partials/decided.html", {"task": task})
    return redirect("dashboard:home")


@login_required
@require_POST
@require_role("reviewer")
def run_marketing(request):
    from agents.marketing.tasks import write_script

    product = get_object_or_404(Product, tenant=request.tenant, slug=request.POST.get("product"))
    try:
        enqueue(write_script, product.id, "manual")
        messages.success(request, f"The Marketing agent is writing a script for {product.name}. "
                                  f"It appears under “Needs your decision” in a few seconds.")
    except Exception as exc:
        hint = ""
        if "6379" in str(exc) or "redis" in str(exc).lower():
            hint = " The background worker (Redis) isn't running. Without Docker, set CELERY_EAGER=1 in .env and restart."
        messages.error(request, f"Could not start: {exc}.{hint}")
    if request.headers.get("HX-Request"):
        resp = HttpResponse()
        resp["HX-Refresh"] = "true"
        return resp
    return redirect("dashboard:home")


@login_required
@require_role("reviewer")
@require_POST
def flow_action(request, task_id):
    """Buttons on a failed Short: try again, or hide it from Today."""
    from agents.marketing.tasks import make_video, write_script

    root = get_object_or_404(Task, id=task_id, tenant=request.tenant, kind="short_script")
    cur = _latest(root)
    if request.POST.get("action") == "retry" and cur.status == "failed" and root.product:
        if cur.kind == "short_video":
            enqueue(make_video, root.id)
            messages.success(request, "Trying the video again. It takes a few minutes.")
        else:
            enqueue(write_script, root.product.id, "manual")
            messages.success(request, "Writing a new script.")
    retry_video = request.POST.get("action") == "retry" and cur.kind == "short_video"
    if not retry_video:  # a new script replaces this Short, or the user removed it
        root.result = {**(root.result or {}), "hidden": True}
        root.save(update_fields=["result"])
    return redirect("dashboard:home")


@login_required
def running(request):
    """Polled by the dashboard every few seconds while something is in production."""
    tasks = Task.objects.filter(tenant=request.tenant, status="running").select_related("product")
    resp = render(request, "dashboard/partials/running.html", {"running": tasks})
    if not tasks:
        resp["HX-Refresh"] = "true"  # job finished: reload to show it in the inbox
    return resp


@login_required
@require_POST
def switch_tenant(request):
    request.session["tenant"] = request.POST.get("tenant")
    return redirect("dashboard:home")


@login_required
def videos(request):
    if (resp := _need_tenant(request)) is not None:
        return resp
    qs = (Task.objects.filter(tenant=request.tenant, kind="short_video").exclude(product__status="archived")
          .select_related("product").order_by("-updated"))
    ready = [v for v in qs.filter(status="approved")[:60] if not (v.result or {}).get("deleted")]
    for v in ready:
        v.mb = _folder_mb(_video_dir(v))
    # rejected, failed and replaced versions still on disk
    leftovers = [v for v in qs.exclude(status__in=["approved", "awaiting_approval", "running"])
                 if not (v.result or {}).get("deleted") and _video_dir(v).exists()]
    return render(request, "dashboard/videos.html", {
        "ready": ready,
        "waiting": qs.filter(status="awaiting_approval")[:12],
        "leftovers": len(leftovers),
        "leftover_mb": round(sum(_folder_mb(_video_dir(v)) for v in leftovers), 1),
    })


def _video_dir(task):
    return Path(settings.MEDIA_ROOT) / "videos" / task.tenant.slug / str(task.id)


def _folder_mb(folder):
    if not folder.exists():
        return 0
    return round(sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_048_576, 1)


def _delete_video_files(task):
    folder = _video_dir(task)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    task.result = {**(task.result or {}), "deleted": True}
    task.save(update_fields=["result"])


@login_required
@require_role("reviewer")
@require_POST
def video_delete(request, task_id):
    """Delete a video's files from disk to save space. The history line stays."""
    task = get_object_or_404(Task, id=task_id, tenant=request.tenant, kind="short_video")
    if task.status in ("running", "awaiting_approval"):
        messages.error(request, "Approve or reject this video first.")
        return redirect("dashboard:videos")
    mb = _folder_mb(_video_dir(task))
    _delete_video_files(task)
    messages.success(request, f"Deleted “{str((task.result or {}).get('title') or task.title)[:60]}” ({mb} MB freed).")
    return redirect("dashboard:videos")


@login_required
@require_role("reviewer")
@require_POST
def videos_cleanup(request):
    """Delete every rejected, failed or replaced video in one go."""
    qs = Task.objects.filter(tenant=request.tenant, kind="short_video").exclude(
        status__in=["approved", "awaiting_approval", "running"])
    freed, count = 0, 0
    for v in qs:
        if (v.result or {}).get("deleted") or not _video_dir(v).exists():
            continue
        freed += _folder_mb(_video_dir(v))
        _delete_video_files(v)
        count += 1
    messages.success(request, f"Deleted {count} unused video{'s' if count != 1 else ''} ({round(freed, 1)} MB freed).")
    return redirect("dashboard:videos")


@login_required
def advanced(request):
    """The engine room: AI team, budgets, learned rules, activity. Hidden from the main tabs."""
    if (resp := _need_tenant(request)) is not None:
        return resp
    t = request.tenant
    agents = sorted(AgentCard.objects.filter(tenant=t, is_current=True), key=lambda a: ({"active": 0}.get(a.status, 1), a.key))
    for a in agents:
        a.spent = a.spent_this_month()
        a.pct = int(min(100, (a.spent / a.monthly_budget_usd * 100) if a.monthly_budget_usd else 0))
    return render(request, "dashboard/advanced.html", {
        "agents": agents, "rules": Rule.objects.filter(tenant=t, active=True)[:30],
        "events": Event.objects.filter(tenant=t).select_related("task")[:60], "dry_run": is_dry_run(t),
    })
