"""AI still images for video scenes (the budget workhorse, about $0.04 each).

Dry run: draws a clearly-labelled placeholder with Pillow, so the whole video
pipeline can be tested for free.
"""
from pathlib import Path

from PIL import Image, ImageDraw

from .fonts import font


def generate(prompt: str, out: Path, *, model: str, dry_run: bool, label: str = "", accent: str = "#C2410C", api_key: str = "") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return _placeholder(out, label or prompt, accent)

    from google.genai import types

    from .genai_client import client

    resp = client(api_key).models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="9:16"),
        ),
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []) or []:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                out.write_bytes(part.inline_data.data)
                return out
    raise RuntimeError("Image model returned no image (possibly blocked by safety filters)")


def _placeholder(out: Path, text: str, accent: str) -> Path:
    w, h = 1080, 1920
    img = Image.new("RGB", (w, h))
    top, bottom = _hex(accent), (30, 28, 43)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    for i, r in enumerate((520, 380, 240)):
        draw.ellipse([w // 2 - r, h // 2 - r - 150, w // 2 + r, h // 2 + r - 150], outline=(255, 255, 255), width=4 + i * 2)
    draw.text((w // 2, 320), "SAMPLE IMAGE (dry run)", font=font(44, bold=True), fill="white", anchor="mm")
    _wrap(draw, text[:220], (80, 1300, w - 80), font(40), "white")
    img.save(out.with_suffix(".png"))
    return out.with_suffix(".png")


def _wrap(draw, text, box, fnt, fill):
    x0, y, x1 = box
    words, line = text.split(), ""
    for word in words:
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) > x1 - x0 and line:
            draw.text(((x0 + x1) // 2, y), line, font=fnt, fill=fill, anchor="ma")
            y += int(fnt.size * 1.3)
            line = word
        else:
            line = test
    if line:
        draw.text(((x0 + x1) // 2, y), line, font=fnt, fill=fill, anchor="ma")


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
