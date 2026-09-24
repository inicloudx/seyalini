"""One door to every AI model (Gemini, OpenAI, Claude...) via LiteLLM.

Switching model = change one line in the agent card. Each call uses the calling
organisation's key. In dry-run mode the call returns sample output and costs 0.
"""
import json
import re
from dataclasses import dataclass
from decimal import Decimal

import litellm

litellm.suppress_debug_info = True


@dataclass
class LLMResult:
    text: str
    cost_usd: Decimal
    tokens: int
    model: str
    dry_run: bool


def complete(model: str, messages: list[dict], *, temperature: float = 0.7, json_mode: bool = False,
             mock: str | None = None, api_key: str = "", dry_run: bool | None = None) -> LLMResult:
    dry = (not api_key) if dry_run is None else dry_run
    kwargs = {"model": model, "messages": messages, "temperature": temperature}
    if api_key and not dry:
        kwargs["api_key"] = api_key  # this organisation's own key
    if json_mode and not dry:
        kwargs["response_format"] = {"type": "json_object"}
    if dry:
        kwargs["mock_response"] = mock if mock is not None else "{}"
    resp = litellm.completion(**kwargs)
    text = resp.choices[0].message.content or ""
    tokens = getattr(getattr(resp, "usage", None), "total_tokens", 0) or 0
    cost = Decimal("0")
    if not dry:
        try:
            cost = Decimal(str(litellm.completion_cost(completion_response=resp)))
        except Exception:  # unknown price for a new model: log tokens, cost 0
            cost = Decimal("0")
    return LLMResult(text=text, cost_usd=cost, tokens=int(tokens), model=model, dry_run=dry)


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```fences``` or add a sentence; be forgiving."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start:end + 1])
