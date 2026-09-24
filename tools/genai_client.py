"""Google GenAI client (Gemini images + Veo video), one per API key (= per organisation)."""
from functools import lru_cache


@lru_cache(maxsize=32)
def client(api_key: str):
    from google import genai

    if not api_key:
        raise RuntimeError("This organisation has no Gemini API key. Add it in Settings.")
    return genai.Client(api_key=api_key)
