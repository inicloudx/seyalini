"""Talk to the Earner from anywhere: the Seyalini chat page or Telegram on your phone.

- It messages you: new money ideas, "Can I create this agent? Here is why", drafts from its team,
  verdicts. Each comes with Yes / No buttons.
- You message it in plain words ("how much did we make?", "yes, create it", "we earned 800 from the
  resume idea", "find ideas I can sell in dollars") and it answers, and does what you clearly asked.

One brain, many apps: every app just passes text in and shows the reply and buttons.
"""
import json
import logging

from django.db import transaction

from agents.llm import parse_json
from agents.runtime import AgentRuntime
from core.models import AgentCard, ChatMessage, Membership, Task

from .earner import HOUSE_RULES, ledger_totals

log = logging.getLogger("seyalini")

HISTORY = 12
PENDING_KINDS = {"money_idea": "money idea", "agent_proposal": "new agent", "work_output": "draft",
                 "short_script": "Short script", "short_video": "Short video"}

CHAT_SYSTEM = """You are the Earner, {tenant}'s money-making AI agent, chatting with the owner{owner} on
their phone. You think like a world-class business builder and speak like a sharp, friendly partner:
short, plain words, no jargon, a few lines at most. Answer in the language the owner writes in.

What you know right now:
{state}

You can act, but ONLY when the owner's latest message clearly asks for it. If unsure, ask a short question.
Actions:
- {{"do": "approve", "task_id": N}}          yes to a money idea, a new agent, a draft, a Short
- {{"do": "reject", "task_id": N, "reason": "why"}}
- {{"do": "change", "task_id": N, "reason": "what to change"}}   redo it with this note
- {{"do": "find_ideas", "note": "what to aim for"}}
- {{"do": "record_money", "plan_id": N, "kind": "earned" or "spent", "amount_inr": N, "note": "..."}}
- {{"do": "stop_experiment", "plan_id": N, "reason": "why"}}
- {{"do": "propose_agent", "plan_id": N, "name": "...", "role": "...", "every": "day" or "week", "instructions": "..."}}
      a new helper agent for an experiment; the owner still gets a Yes / No to create it
- {{"do": "run_agent" or "pause_agent" or "restart_agent", "key": "agent key"}}

{rules}
Reply with ONLY a JSON object: {{"reply": "your message to the owner", "actions": [ ... ]}}"""


# --- sending ---------------------------------------------------------------------------------------

def notify(tenant, text: str, buttons: list | None = None) -> ChatMessage:
    """The Earner speaks first. Saved in the chat and sent to every connected app."""
    from tools import telegram

    msg = ChatMessage.objects.create(tenant=tenant, role="agent", channel="all", text=text[:4000], buttons=buttons or [])
    telegram.send_chat(tenant, msg.text, msg.buttons)
    return msg


def yes_no(task: Task, yes: str, no: str) -> list:
    return [[yes, f"d:{task.id}:approved"], [no, f"d:{task.id}:discarded"]]


# --- understanding -------------------------------------------------------------------------------

def _state(tenant) -> str:
    from dashboard.money_views import money_summary

    from . import team

    s = money_summary(tenant)
    lines = [f"Money so far: earned Rs {s['earned']}, spent Rs {s['spent']}, profit Rs {s['profit']}."]
    pending = Task.objects.filter(tenant=tenant, status="awaiting_approval", kind__in=PENDING_KINDS)[:12]
    if pending:
        lines.append("Waiting for the owner's yes/no:")
        for t in pending:
            r = t.result or {}
            detail = r.get("how_it_makes_money") or r.get("role") or r.get("note_for_owner") or r.get("hook") or ""
            lines.append(f"- task #{t.id} ({PENDING_KINDS[t.kind]}): {t.title}. {detail}"[:300])
    plans = Task.objects.filter(tenant=tenant, kind="money_plan", status="done")[:10]
    if plans:
        lines.append("Experiments:")
        for p in plans:
            r, tot = p.result or {}, ledger_totals(p)
            lines.append(f"- plan #{p.id} {p.title}: {r.get('state')}, goal {r.get('goal', '?')}, earned Rs {tot['earned']}, "
                         f"spent Rs {tot['spent']} of Rs {r.get('budget_inr', '?')}, decision date {str(r.get('ends', ''))[:10]}")
    crew = team.workers(tenant)
    if crew:
        lines.append("Your agent team: " + "; ".join(f"{c.name} (key {c.key}, {c.status}, every {(c.config or {}).get('every')})"
                                                   for c in crew))
    return "\n".join(lines)


def _owner_user(tenant):
    m = Membership.objects.filter(tenant=tenant, role="owner").select_related("user").order_by("id").first()
    return m.user if m else None


def _task(tenant, task_id, **filters):
    try:
        return Task.objects.filter(tenant=tenant, id=int(task_id), **filters).first()
    except (TypeError, ValueError):
        return None


def act(tenant, action: dict, user=None) -> str:
    """Do one thing the owner asked for. Returns a short line saying what happened."""
    from core.approvals import decide
    from core.jobs import enqueue

    from . import earner, team

    do = action.get("do")
    user = user or _owner_user(tenant)
    if do in ("approve", "reject", "change"):
        t = _task(tenant, action.get("task_id"), status="awaiting_approval", kind__in=PENDING_KINDS)
        if not t:
            return "That one is already decided or I can't find it."
        decision = {"approve": "approved", "reject": "discarded", "change": "redo"}[do]
        decide(t, user, decision, action.get("reason", ""))
        word = {"approved": "✓ Yes", "discarded": "✕ No", "redo": "↻ Redoing"}[decision]
        return f"{word}: {t.title}"
    if do == "find_ideas":
        from .tasks import run_hunt

        transaction.on_commit(lambda: enqueue(run_hunt, tenant.id, None, str(action.get("note", ""))[:200]))
        return "🔎 Looking for new ways to earn. I'll message you the ideas."
    if do in ("record_money", "stop_experiment", "propose_agent"):
        plan = _task(tenant, action.get("plan_id"), kind="money_plan", status="done")
        if not plan:
            return "I can't find that experiment."
        if do == "record_money":
            try:
                tot = earner.record(plan, action.get("kind"), int(action.get("amount_inr") or 0), action.get("note", ""), user)
            except (TypeError, ValueError):
                return "I need an amount above zero, and whether it was earned or spent."
            return f"📒 Noted. {plan.title}: profit Rs {tot['profit']:,} so far."
        if do == "stop_experiment":
            earner.stop(plan, action.get("reason", ""))
            return f"⏹ Stopped {plan.title}. Its agents are paused."
        made = team.propose(plan, [action], why=action.get("why") or "You asked for it")
        return f"Proposed {made[0].result['name']}. Say yes to create it." if made else "I couldn't add that agent (team full, or it exists)."
    if do in ("run_agent", "pause_agent", "restart_agent"):
        card = AgentCard.objects.filter(tenant=tenant, key=action.get("key"), is_current=True, config__kind="worker").first()
        if not card:
            return "I can't find that agent."
        if do == "run_agent":
            from agents.worker.tasks import run_worker

            if card.status != "active":
                return f"{card.name} is paused."
            transaction.on_commit(lambda: enqueue(run_worker, tenant.id, card.key))
            return f"▶ {card.name} is working on it."
        new = team.set_status(card, "paused" if do == "pause_agent" else "active",
                              "Paused by you in chat" if do == "pause_agent" else "Restarted by you in chat")
        return f"{new.name} is {'paused' if new.status == 'paused' else 'working again'}."
    return ""


def press(tenant, data: str, user=None) -> str:
    """A button was tapped: "d:<task_id>:<decision>"."""
    try:
        kind, task_id, decision = data.split(":")
    except ValueError:
        return "Unknown button."
    if kind != "d" or decision not in ("approved", "discarded"):
        return "Unknown button."
    ChatMessage.objects.create(tenant=tenant, role="owner", channel="button", text="Yes" if decision == "approved" else "No")
    line = act(tenant, {"do": "approve" if decision == "approved" else "reject", "task_id": task_id}, user)
    t = _task(tenant, task_id)
    if t and decision == "approved":
        line += {"money_idea": "\nWriting the 14-day plan now.",
                 "agent_proposal": "\nCreating the agent. Its first work comes soon."}.get(t.kind, "")
    ChatMessage.objects.create(tenant=tenant, role="agent", channel="button", text=line)
    return line


def handle(tenant, text: str, channel: str = "web", user=None) -> ChatMessage:
    """The owner wrote something. Understand it, act on it, answer."""
    text = (text or "").strip()[:2000]
    history = list(ChatMessage.objects.filter(tenant=tenant).order_by("-created")[:HISTORY])[::-1]
    ChatMessage.objects.create(tenant=tenant, role="owner", channel=channel, text=text)
    agent = AgentRuntime(tenant, "earner")
    owner = (tenant.settings or {}).get("owner_name")
    msgs = [{"role": "system", "content": CHAT_SYSTEM.format(tenant=tenant.name, owner=f" ({owner})" if owner else "",
                                                             state=_state(tenant), rules=HOUSE_RULES)}]
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
        agent.log("earner_note", f"Chat reply failed: {str(exc)[:150]}")
        text_out = "Sorry, I couldn't think just now (AI budget or connection). Try again in a bit."
    return ChatMessage.objects.create(tenant=tenant, role="agent", channel=channel, text=text_out[:4000])


def _sample_reply(tenant) -> str:
    from dashboard.money_views import money_summary

    s = money_summary(tenant)
    return (f"(Practice mode) So far: earned ₹{s['earned']}, profit ₹{s['profit']}, {s['running']} test(s) running, "
            f"{s['ideas']} idea(s) waiting for you.")
