"""Veo video clips (Gemini API). The premium option: use for the hook scene only
(hybrid mode) to keep cost down. The Gemini Developer API always adds Veo's own sound;
the editor mixes in the music."""
import time
from pathlib import Path

NEGATIVE = "text, letters written on screen, watermark, logo, real human faces, children's faces, scary, violence, dark gloomy lighting"


def generate(prompt: str, out: Path, *, model: str, api_key: str = "", seconds: int = 8, resolution: str = "720p",
             timeout_s: int = 600) -> Path:
    from google.genai import types

    from .genai_client import client

    out.parent.mkdir(parents=True, exist_ok=True)
    c = client(api_key)
    # Not every Veo model accepts every option (e.g. Veo 3.1 Lite on the Gemini Developer API
    # rejects negativePrompt). Start with all of them and drop any the API says it doesn't support.
    options = {"aspect_ratio": "9:16", "duration_seconds": seconds, "resolution": resolution,
               "negative_prompt": NEGATIVE, "number_of_videos": 1}
    full_prompt = f"{prompt} Avoid: {NEGATIVE}."
    for _ in range(len(options)):
        try:
            op = c.models.generate_videos(model=model, prompt=full_prompt,
                                          config=types.GenerateVideosConfig(**options))
            break
        except Exception as exc:
            msg = str(exc)
            bad = next((k for k in options if k.replace("_", "") in msg.replace("_", "").lower()
                        or _camel(k) in msg), None)
            if "INVALID_ARGUMENT" not in msg or bad is None or bad == "aspect_ratio":
                raise
            options.pop(bad)
    else:
        raise RuntimeError("Veo rejected every option set")
    waited = 0
    while not op.done:
        if waited > timeout_s:
            raise TimeoutError(f"Veo did not finish in {timeout_s}s")
        time.sleep(10)
        waited += 10
        op = c.operations.get(op)
    if getattr(op, "error", None):
        raise RuntimeError(f"Veo error: {op.error}")
    vids = getattr(op.response, "generated_videos", None) or []
    if not vids:
        raise RuntimeError("Veo returned no video (possibly blocked by safety filters)")
    video = vids[0].video
    c.files.download(file=video)
    video.save(str(out))
    return out


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)
