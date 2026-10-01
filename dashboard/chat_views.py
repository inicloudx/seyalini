"""Chat with the Manager: in Seyalini, and from Telegram on your phone."""
import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from agents.manager import chat
from core.jobs import enqueue
from core.models import ChatMessage, Tenant
from core.roles import require_role
from core.secrets import get_secret, set_secret


@login_required
@require_role("reviewer")
def chat_page(request):
    if request.tenant is None:
        return render(request, "dashboard/no_tenant.html")
    msgs = list(ChatMessage.objects.filter(tenant=request.tenant).order_by("-created", "-id")[:60])[::-1]
    return render(request, "dashboard/chat.html", {
        "chat": msgs, "telegram": bool(get_secret(request.tenant, "TELEGRAM_CHAT_ID")),
    })


@login_required
@require_role("reviewer")
@require_POST
def chat_send(request):
    t = request.tenant
    if action := request.POST.get("action"):
        chat.press(t, action, request.user)
    elif text := request.POST.get("text", "").strip():
        chat.handle(t, text, "web", request.user)
    if request.headers.get("HX-Request"):
        msgs = list(ChatMessage.objects.filter(tenant=t).order_by("-created", "-id")[:60])[::-1]
        return render(request, "dashboard/partials/chat_messages.html", {"chat": msgs})
    return redirect("dashboard:chat")


# --- Telegram ----------------------------------------------------------------------------------------

def process_update(tenant, update: dict):
    """One Telegram update (a message or a button tap), from the webhook or the laptop poller."""
    from tools import telegram

    linked = get_secret(tenant, "TELEGRAM_CHAT_ID")
    if cb := update.get("callback_query"):
        chat_id = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
        token = get_secret(tenant, "TELEGRAM_BOT_TOKEN")
        try:
            telegram.call(token, "answerCallbackQuery", {"callback_query_id": cb.get("id")})
        except Exception:
            pass
        if linked and chat_id == linked:
            telegram.send_chat(tenant, chat.press(tenant, cb.get("data", "")))
        return
    msg = update.get("message") or {}
    chat_id, text = str((msg.get("chat") or {}).get("id", "")), (msg.get("text") or "").strip()
    if not chat_id:
        return
    link = (tenant.settings or {}).get("telegram_link") or {}
    if text.split()[:1] in (["/start"], ["/link"]) and len(text.split()) > 1:
        if link.get("code") and text.split()[1] == link["code"]:
            set_secret(tenant, "TELEGRAM_CHAT_ID", chat_id)
            sett = dict(tenant.settings or {})
            sett.pop("telegram_link", None)
            tenant.settings = sett
            tenant.save(update_fields=["settings"])
            telegram.send_chat(tenant, f"Connected to {tenant.name}. I'm your Manager: I'll send every script and video here "
                                       f"for a Yes or No, tell you when Shorts are posted, and you can ask me anything.")
        else:
            telegram.send_chat(tenant, "That code is not right or has expired. Get a new one in Seyalini Settings.", chat_id=chat_id)
        return
    if not linked or chat_id != linked:
        telegram.send_chat(tenant, "This is a private assistant. Connect it from Seyalini Settings first.", chat_id=chat_id)
        return
    if not text:
        telegram.send_chat(tenant, "I can read text messages for now. Type your message, please.")
        return
    reply = chat.handle(tenant, text, "telegram")
    telegram.send_chat(tenant, reply.text, reply.buttons)


@csrf_exempt
@require_POST
def telegram_webhook(request, slug):
    from tools import telegram

    tenant = get_object_or_404(Tenant, slug=slug)
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != telegram.webhook_secret(tenant):
        return HttpResponseForbidden("bad secret")
    try:
        update = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    from agents.manager.tasks import telegram_update

    # answer Telegram at once; think in the background
    enqueue(telegram_update, tenant.id, update, mode=settings.CHAT_JOBS_MODE)
    return JsonResponse({"ok": True})


@login_required
@require_role("owner")
@require_POST
def telegram_connect(request):
    from tools import telegram

    t = request.tenant
    token = get_secret(t, "TELEGRAM_BOT_TOKEN")
    if not token:
        messages.error(request, "Save your Telegram bot token first (from @BotFather).")
        return redirect(reverse("dashboard:settings") + "#chat")
    sett = dict(t.settings or {})
    try:
        me = telegram.call(token, "getMe")
        sett["telegram_bot"] = me.get("username", "")
    except Exception as exc:
        messages.error(request, f"Telegram did not accept the bot token: {str(exc)[:120]}")
        return redirect(reverse("dashboard:settings") + "#chat")
    sett["telegram_link"] = {"code": f"{secrets.randbelow(900000) + 100000}"}
    t.settings = sett
    t.save(update_fields=["settings"])
    base = settings.DASHBOARD_URL.rstrip("/")
    if base.startswith("https://"):
        try:
            telegram.call(token, "setWebhook", {"url": f"{base}{reverse('telegram_webhook', args=[t.slug])}",
                                                "secret_token": telegram.webhook_secret(t),
                                                "allowed_updates": ["message", "callback_query"]})
        except Exception as exc:
            messages.error(request, f"Could not set up Telegram replies: {str(exc)[:120]}")
    else:
        messages.success(request, "On a laptop, keep `python manage.py telegram_poll` running in a second window so replies arrive.")
    return redirect(reverse("dashboard:settings") + "#chat")
