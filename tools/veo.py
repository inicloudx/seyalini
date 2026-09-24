"""Veo video clips (Gemini API). The premium option: use for the hook scene only
(hybrid mode) to keep cost down. Audio is off; the editor adds music."""
import time
from pathlib import Path

NEGATIVE = "text, letters written on screen, watermark, logo, real human faces, children's faces, scary, violence, dark gloomy lighting"


def generate(prompt: str, out: Path, *, model: str, api_key: str = "", seconds: int = 8, resolution: str = "720p",
             timeout_s: int = 600) -> Path:
    from google.genai import types

    from .genai_client import client

    out.parent.mkdir(parents=True, exist_ok=True)
    c = client(api_key)
    op = c.models.generate_videos(
        model=model,
        prompt=prompt,
        config=types.GenerateVideosConfig(
            aspect_ratio="9:16",
            duration_seconds=seconds,
            resolution=resolution,
            negative_prompt=NEGATIVE,
            generate_audio=False,
            number_of_videos=1,
        ),
    )
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
