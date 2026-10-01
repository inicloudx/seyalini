"""The Manager: your line to the whole marketing team, in the Seyalini chat and on Telegram.

- It messages you first: every script and every finished video (the video itself on Telegram) with
  ✓ Approve / ↻ Change / ✕ Reject buttons, "Posted to YouTube", and anything that failed.
- "↻ Change" asks what to change; your next message becomes the redo note (and a lesson the agent keeps).
- You ask in plain words ("what's waiting?", "make a new Short", "how many views?") and it answers,
  and does what you clearly asked. It never publishes anything you did not approve.

One brain, many apps: every app just passes text in and shows the reply and buttons.
"""
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from agents.llm import parse_json
from agents.runtime import AgentRuntime
from core.models import ChatMessage, Event, Membership, Product, Task

log = logging.getLogger("seyalini")

HISTORY = 12
PENDING_KINDS = {"short_script": "script", "short_video": "video", "view_diagnosis": "views check"}

CHAT_SYSTEM = """You are the Manager of {tenant}'s AI marketing team (Seyalini), chatting with the owner{owner}
on their phone. The team's goal: grow the audience and installs of the owner's apps with daily YouTube Shorts.
Your team: Scout (studies the most-viewed Shorts at 6 AM), Marketing (writes a script at 7 AM and makes the
video after the owner approves the script), Publisher (posts approved videos to YouTube), Analyst (reads
views at 9 PM and tells the team which ideas work).
Speak like a sharp, friendly manager: short, plain words, no jargon, a few lines at most. Answer in the
language the owner writes in. Be honest about numbers; never invent them.

What you know right now:
{state}

You can act, but ONLY when the owner's latest message clearly asks for it. If unsure, ask a short question.
Actions:
- {{"do": "approve", "task_id": N}}                         yes to a script or a video
- {{"do": "reject", "task_id": N, "reason": "why"}}         throw it away
- {{"do": "change", "task_id": N, "reason": "what to change"}}   redo it with this note
- {{"do": "new_short", "app": "app slug"}}                  write a new script now
- {{"do": "check_views"}}                                   read the latest YouTube numbers now
- {{"do": "study_trends"}}                                  the Scout studies today's top Shorts again
- {{"do": "diagnose_views", "app": "app slug", "note": "optional focus"}}   why so few views? the Analyst checks
      our Shorts against the top ones and proposes 3 fixes (use it when the owner asks why views are low)
- {{"do": "use_fixes", "task_id": N, "fixes": [1, 3]}}      use only some fixes of a views check (approve = all)
Nothing is ever posted without the owner's approval of the video.
Reply with ONLY a JSON object: {{"reply": "your message to the owner", "actions": [ ... ]}}"""


# --- sending ---------------------------------------------------------------------------------------

def notify(tenant, text: str, buttons: list | None = None) -> ChatMessage | None:
    """The Manager speaks first. Saved in the chat and sent to Telegram. Never breaks the caller."""
    from tools import telegram

    try:
        msg = ChatMessage.objects.create(tenant=tenant, role="agent", channel="all", text=text[:4000], buttons=buttons or [])
        telegram.send_chat(tenant, msg.text, msg.buttons)
        return msg
    except Exception as exc:
        log.warning("Manager could not notify: %s", exc)
        return None


def decision_buttons(task: Task) -> list:
    return [["✓ Approve", f"d:{task.id}:approved"], ["↻ Change", f"r:{task.id}"], ["✕ Reject", f"d:{task.id}:discarded"]]


def ask_script(task: Task):
    s = task.result or {}
    scenes = "\n".join(f"  {i}. {sc.get('on_screen_text', '')}" for i, sc in enumerate(s.get("scenes") or [], 1))
    pattern = f"\nProven pattern: {s['pattern']}" if s.get("pattern") else ""
    notify(task.tenant, f"📝 New script for {task.product.name}: {s.get('title', '')}\n"
                        f"Hook: “{s.get('hook', '')}”{pattern}\n{scenes}\n\n"
                        f"Approve and I'll make the video.", decision_buttons(task))


def ask_video(task: Task, path):
    """The video itself on Telegram, with the buttons right under it."""
    from tools import telegram

    r = task.result or {}
    text = (f"🎬 Video ready for {task.product.name}: {r.get('title', '')}\n"
            f"{r.get('seconds', 0):.0f}s{' · voice-over' if r.get('voice') else ''}. Approve and it posts to YouTube.")
    buttons = decision_buttons(task)
    try:
        msg = ChatMessage.objects.create(tenant=task.tenant, role="agent", channel="all", text=text, buttons=buttons)
        if not telegram.send_video(task.tenant, path, msg.text, buttons):
            telegram.send_chat(task.tenant, msg.text, buttons)  # too big or failed: the buttons still arrive
    except Exception as exc:
        log.warning("Manager could not send the video: %s", exc)


def ask_diagnosis(task: Task):
    r = task.result or {}
    causes = "\n".join(f"{i}. {c.get('cause', '')}: {c.get('evidence', '')}" for i, c in enumerate(r.get("causes") or [], 1))
    fixes = "\n".join(f"{i}. [{'Marketing' if f.get('who') == 'marketing' else 'You'}] {f['fix']}"
                      for i, f in enumerate(r.get("fixes") or [], 1))
    notify(task.tenant, f"🔍 Why so few views: {task.product.name}\n{r.get('summary', '')}\n\nWhy:\n{causes}\n\n"
                        f"Fixes:\n{fixes}\n\nTest this week: {r.get('test', '')}\n\n"
                        f"Use them? Marketing fixes become rules for every new script. "
                        f"(Or say e.g. “use fixes 1 and 3”.)",
           [["✓ Use the fixes", f"d:{task.id}:approved"], ["↻ Look again", f"r:{task.id}"], ["✕ No", f"d:{task.id}:discarded"]])


def posted(task: Task):
    r = task.result or {}
    where = r.get("url") or "(practice mode, nothing uploaded)"
    notify(task.tenant, f"✅ Posted to YouTube ({r.get('privacy', '')}): {task.title.replace('YouTube: ', '')}\n{where}")


# --- understanding -------------------------------------------------------------------------------

def _owner_user(tenant):
    m = Membership.objects.filter(tenant=tenant, role="owner").select_related("user").order_by("id").first()
    return m.user if m else None


def _task(tenant, task_id, **filters):
    try:
        return Task.objects.filter(tenant=tenant, id=int(task_id), **filters).first()
    except (TypeError, ValueError):
        return None


def _state(tenant) -> str:
    now = timezone.now()
    apps = list(Product.objects.filter(tenant=tenant, status="live"))
    lines = ["Apps: " + ("; ".join(f"{p.name} (slug {p.slug}{', marketing on' if (p.config or {}).get('marketing') else ''})"
                                   for p in apps) or "none yet")]
    pending = Task.objects.filter(tenant=tenant, status="awaiting_approval", kind__in=PENDING_KINDS).select_related("product")[:10]
    if pending:
        lines.append("Waiting for the owner's decision:")
        for t in pending:
            r = t.result or {}
            detail = f"Hook: {r['hook']}" if r.get("hook") else r.get("summary", "")
            lines.append(f"- task #{t.id} ({PENDING_KINDS[t.kind]} for {t.product.name if t.product else '?'}): "
                         f"{r.get('title') or t.title}. {detail}"[:300])
    else:
        lines.append("Nothing is waiting for the owner.")
    running = Task.objects.filter(tenant=tenant, status="running").count()
    if running:
        lines.append(f"{running} job(s) running right now (scripts, videos or uploads).")
    posts = [p for p in Task.objects.filter(tenant=tenant, kind="publish_youtube", status="done", created__gte=now - timedelta(days=30))]
    if posts:
        views = sum((p.result or {}).get("stats", {}).get("views", 0) for p in posts)
        best = max(posts, key=lambda p: (p.result or {}).get("stats", {}).get("views", 0))
        lines.append(f"Last 30 days: {len(posts)} Shorts posted, {views:,} YouTube views in total. Best: "
                     f"{best.title.replace('YouTube: ', '')} ({(best.result or {}).get('stats', {}).get('views', 0):,} views).")
    else:
        lines.append("No Shorts posted in the last 30 days.")
    for p in apps:
        cfg = p.config or {}
        for i in (cfg.get("insights") or [])[:2]:
            lines.append(f"Analyst on {p.name}: {i}")
        sc = cfg.get("scout") or {}
        if sc.get("summary"):
            lines.append(f"Scout on {p.name}: {sc['summary']} Patterns: " + ", ".join(x["name"] for x in sc.get("patterns") or []))
    month = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = Event.objects.filter(tenant=tenant, created__gte=month).aggregate(s=Sum("cost_usd"))["s"] or 0
    lines.append(f"AI spend this month: ${float(spent):.2f}.")
    return "\n".join(lines)


def act(tenant, action: dict, user=None) -> str:
    """Do one thing the owner asked for. Returns a short line saying what happened."""
    from core.approvals import decide
    from core.jobs import enqueue

    do = action.get("do")
    user = user or _owner_user(tenant)
    if do in ("approve", "reject", "change"):
        t = _task(tenant, action.get("task_id"), status="awaiting_approval", kind__in=PENDING_KINDS)
        if not t:
            return "That one is already decided or I can't find it."
        decision = {"approve": "approved", "reject": "discarded", "change": "redo"}[do]
        decide(t, user, decision, action.get("reason", ""))
        what = PENDING_KINDS[t.kind]
        next_step = {"short_script": "Making the video now.", "short_video": "Posting it to YouTube.",
                     "view_diagnosis": "The Marketing fixes are now rules for every new script."}[t.kind]
        return {"approved": f"✓ Approved the {what}. {next_step}",
                "discarded": f"✕ Rejected the {what}.",
                "redo": f"↻ Redoing the {what}" + (f", and I'll remember: “{action.get('reason')}”." if action.get("reason") else ".")}[decision]
    if do == "new_short":
        from agents.marketing.tasks import write_script

        qs = Product.objects.filter(tenant=tenant, status="live")
        p = qs.filter(slug=action.get("app")).first() or (qs.first() if qs.count() == 1 else None)
        if not p:
            return "Which app? " + ", ".join(x.name for x in qs)
        transaction.on_commit(lambda: enqueue(write_script, p.id, "manual"))
        return f"✍️ Writing a new script for {p.name}. I'll send it here in a minute."
    if do == "check_views":
        from agents.analyst import analyst
        from agents.publisher.youtube_publisher import is_connected

        if not is_connected(tenant):
            return "YouTube isn't connected yet (Settings → YouTube)."
        n = analyst.run(tenant)
        views = sum((p.result or {}).get("stats", {}).get("views", 0)
                    for p in Task.objects.filter(tenant=tenant, kind="publish_youtube", status="done"))
        return f"📈 Checked {n} Short{'s' if n != 1 else ''}: {views:,} views in total."
    if do == "diagnose_views":
        from agents.analyst.tasks import diagnose_views

        qs = Product.objects.filter(tenant=tenant, status="live")
        p = qs.filter(slug=action.get("app")).first() or (qs.first() if qs.count() == 1 else None)
        if not p:
            return "Which app? " + ", ".join(x.name for x in qs)
        note = str(action.get("note") or "")[:200]
        transaction.on_commit(lambda: enqueue(diagnose_views, p.id, note))
        return f"🔍 Checking why {p.name}'s Shorts get so few views. I'll send the causes and 3 fixes in a minute."
    if do == "use_fixes":
        from agents.analyst.diagnose import apply_fixes

        t = _task(tenant, action.get("task_id"), status="awaiting_approval", kind="view_diagnosis")
        if not t:
            return "That views check is already decided or I can't find it."
        try:
            which = sorted({int(n) for n in action.get("fixes") or []})
        except (TypeError, ValueError):
            return "Tell me the fix numbers, e.g. 1 and 3."
        a = t.approval
        a.decision, a.decided_by, a.decided_at = "approved", user, timezone.now()
        a.save()
        rules = apply_fixes(t, which)
        return f"✓ Using fix{'es' if len(which) != 1 else ''} {', '.join(map(str, which))}: {len(rules)} new rule{'s' if len(rules) != 1 else ''} for the Marketing agent."
    if do == "study_trends":
        from agents.scout.tasks import run_scout

        transaction.on_commit(lambda: enqueue(run_scout, tenant.id))
        return "🔎 The Scout is studying today's top Shorts. New patterns in a couple of minutes."
    return ""


def press(tenant, data: str, user=None) -> str:
    """A button was tapped: "d:<task_id>:<decision>" (approve / reject) or "r:<task_id>" (change)."""
    parts = data.split(":")
    if len(parts) == 2 and parts[0] == "r":
        t = _task(tenant, parts[1], status="awaiting_approval", kind__in=PENDING_KINDS)
        ChatMessage.objects.create(tenant=tenant, role="owner", channel="button", text="Change")
        if not t:
            line = "That one is already decided."
        else:
            sett = dict(tenant.settings or {})
            sett["chat_redo"] = t.id
            tenant.settings = sett
            tenant.save(update_fields=["settings"])
            line = f"What should change in “{(t.result or {}).get('title') or t.title}”? Type it and I'll redo it."
        ChatMessage.objects.create(tenant=tenant, role="agent", channel="button", text=line)
        return line
    if len(parts) != 3 or parts[0] != "d" or parts[2] not in ("approved", "discarded"):
        return "Unknown button."
    decision = parts[2]
    ChatMessage.objects.create(tenant=tenant, role="owner", channel="button", text="Approve" if decision == "approved" else "Reject")
    line = act(tenant, {"do": "approve" if decision == "approved" else "reject", "task_id": parts[1]}, user)
    ChatMessage.objects.create(tenant=tenant, role="agent", channel="button", text=line)
    return line


def _pending_redo(tenant, text: str, user) -> str | None:
    """After "↻ Change", the next message is the redo note."""
    sett = dict(tenant.settings or {})
    task_id = sett.pop("chat_redo", None)
    if not task_id:
        return None
    tenant.settings = sett
    tenant.save(update_fields=["settings"])
    if text.lower() in ("cancel", "no", "never mind", "nevermind"):
        return "Okay, no change."
    return act(tenant, {"do": "change", "task_id": task_id, "reason": text[:300]}, user)


def handle(tenant, text: str, channel: str = "web", user=None) -> ChatMessage:
    """The owner wrote something. Understand it, act on it, answer."""
    text = (text or "").strip()[:2000]
    history = list(ChatMessage.objects.filter(tenant=tenant).order_by("-created", "-id")[:HISTORY])[::-1]
    ChatMessage.objects.create(tenant=tenant, role="owner", channel=channel, text=text)
    if (line := _pending_redo(tenant, text, user or _owner_user(tenant))) is not None:
        return ChatMessage.objects.create(tenant=tenant, role="agent", channel=channel, text=line)
    agent = AgentRuntime(tenant, "manager")
    owner = (tenant.settings or {}).get("owner_name")
    msgs = [{"role": "system", "content": CHAT_SYSTEM.format(tenant=tenant.name, owner=f" ({owner})" if owner else "",
                                                             state=_state(tenant))}]
    msgs += [{"role": "user" if m.role == "owner" else "assistant", "content": m.text} for m in history]
    msgs.append({"role": "user", "content": text})
    try:
        raw = agent.think(msgs, json_mode=True, mock=json.dumps({"reply": _sample_reply(tenant), "actions": []}))
        out = parse_json(raw)
        reply = str(out.get("reply") or "").strip()
        done = []
        for a in (out.get("actions") or [])[:5]:
            if isinstance(a, dict):
                try:
                    line = act(tenant, a, user)
                except Exception as exc:
                    line = f"Could not do that: {str(exc)[:100]}"
                    log.warning("chat action failed: %s", exc)
                if line:
                    done.append(line)
        text_out = "\n\n".join(x for x in [reply, "\n".join(done)] if x) or "Okay."
    except Exception as exc:  # the chat must always answer
        agent.log("manager_note", f"Chat reply failed: {str(exc)[:150]}")
        text_out = "Sorry, I couldn't think just now (AI budget or connection). Try again in a bit."
    return ChatMessage.objects.create(tenant=tenant, role="agent", channel=channel, text=text_out[:4000])


def _sample_reply(tenant) -> str:
    waiting = Task.objects.filter(tenant=tenant, status="awaiting_approval", kind__in=PENDING_KINDS).count()
    posted_n = Task.objects.filter(tenant=tenant, kind="publish_youtube", status="done").count()
    return f"(Practice mode) {waiting} item(s) waiting for you, {posted_n} Short(s) posted so far."
