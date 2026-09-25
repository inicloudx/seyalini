"""The editor: turns scene images/clips into a finished 1080x1920 Short.

Uses the ffmpeg that ships inside the `imageio-ffmpeg` pip package, so nothing
extra has to be installed on Windows, Mac or Linux.
- still images get a slow zoom (Ken Burns) so they feel alive
- on-screen text, a logo badge and an end card are drawn with Pillow
- a spoken voice-over per scene (Gemini TTS), optional background music from assets/music
"""
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageOps

from .fonts import font

W, H, FPS = 1080, 1920, 30


@dataclass
class Scene:
    source: Path          # .png/.jpg image or .mp4 clip
    seconds: float
    text: str = ""
    voice: Path | None = None   # voice-over WAV for this scene


def ffmpeg(*args, cwd: Path | None = None):
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *map(str, args)]
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[-600:]}")


# --- overlays drawn with Pillow ----------------------------------------------
def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _wrap_lines(draw, text, fnt, max_w):
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) > max_w and line:
            lines.append(line)
            line = word
        else:
            line = test
    return lines + ([line] if line else [])


def overlay_png(path: Path, text: str, badge: str, accent: str, logo: Path | None) -> Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # logo / badge top-left
    if logo and logo.exists():
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((220, 220))
        img.alpha_composite(lg, (48, 72))
    else:
        f = font(34, bold=True)
        tw = int(d.textlength(badge, font=f))
        d.rounded_rectangle([48, 72, 48 + tw + 48, 72 + 70], radius=35, fill=(*_hex(accent), 235))
        d.text((48 + 24, 72 + 35), badge, font=f, fill="white", anchor="lm")
    # caption lower third
    if text:
        f = font(66, bold=True)
        lines = _wrap_lines(d, text, f, W - 200)[:3]
        lh = int(f.size * 1.25)
        box_h = lh * len(lines) + 60
        y0 = int(H * 0.70)
        d.rounded_rectangle([60, y0, W - 60, y0 + box_h], radius=36, fill=(20, 20, 26, 200))
        for i, ln in enumerate(lines):
            d.text((W // 2, y0 + 30 + i * lh + lh // 2), ln, font=f, fill="white", anchor="mm")
    img.save(path)
    return path


def end_card_png(path: Path, app_name: str, cta: str, accent: str, logo: Path | None) -> Path:
    img = Image.new("RGB", (W, H), _hex(accent))
    d = ImageDraw.Draw(img)
    for i in range(H):  # smooth top-to-bottom darkening for depth
        d.line([(0, i), (W, i)], fill=tuple(max(0, c - int(50 * i / H)) for c in _hex(accent)))
    y = 560
    if logo and logo.exists():
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((360, 360))
        img.paste(lg, ((W - lg.width) // 2, y - 220), lg)
        y += 200
    d.text((W // 2, y), app_name, font=font(96, bold=True), fill="white", anchor="mm")
    fy = y + 140
    for ln in _wrap_lines(d, cta, font(52), W - 160)[:3]:
        d.text((W // 2, fy), ln, font=font(52), fill="white", anchor="mm")
        fy += 70
    d.rounded_rectangle([W // 2 - 330, fy + 80, W // 2 + 330, fy + 210], radius=65, fill="white")
    d.text((W // 2, fy + 145), "Get it on Google Play", font=font(50, bold=True), fill=_hex(accent), anchor="mm")
    img.save(path)
    return path


def _prep_image(src: Path, dst: Path) -> Path:
    """Make a 9:16 frame, 1.5x size so the zoom stays sharp.
    Portrait images are cover-cropped. Landscape ones (like store screenshots) are shown
    whole on a blurred, enlarged copy of themselves, so nothing important is cut off."""
    from PIL import ImageFilter

    im = Image.open(src).convert("RGB")
    size = (int(W * 1.5), int(H * 1.5))
    if im.width / im.height <= 0.75:
        ImageOps.fit(im, size, Image.LANCZOS).save(dst, quality=92)
        return dst
    bg = ImageOps.fit(im, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), 0.35)
    fg = ImageOps.contain(im, (int(size[0] * 0.94), int(size[1] * 0.6)), Image.LANCZOS)
    bg.paste(fg, ((size[0] - fg.width) // 2, int(size[1] * 0.28)))
    bg.save(dst, quality=92)
    return dst


# --- segments -------------------------------------------------------------------
def _segment(scene: Scene, overlay: Path, out: Path, idx: int):
    frames = int(scene.seconds * FPS)
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    if scene.source.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration={scene.seconds}[b];[b][1:v]overlay=0:0")
        ffmpeg("-i", scene.source, "-i", overlay, "-filter_complex", vf, "-t", scene.seconds, *enc, out)
    else:
        prepared = _prep_image(scene.source, out.with_suffix(".jpg"))
        zin = idx % 2 == 0  # alternate zoom in / zoom out for variety
        z = f"min(1+0.12*on/{frames},1.12)" if zin else f"max(1.12-0.12*on/{frames},1.0)"
        vf = (f"[0:v]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}[b];"
              f"[b][1:v]overlay=0:0")
        ffmpeg("-i", prepared, "-i", overlay, "-filter_complex", vf, "-frames:v", frames, *enc, out)
    return out


def render(scenes: list[Scene], out: Path, *, badge: str, accent: str, app_name: str, cta: str,
           logo: Path | None = None, music_dir: Path | None = None, end_seconds: float = 3.0) -> dict:
    work = out.parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, sc in enumerate(scenes):
        ov = overlay_png(work / f"ov{i}.png", sc.text, badge, accent, logo)
        parts.append(_segment(sc, ov, work / f"seg{i}.mp4", i))
    end_img = end_card_png(work / "end.png", app_name, cta, accent, logo)
    parts.append(_segment(Scene(end_img, end_seconds), _transparent(work / "ov_blank.png"), work / "seg_end.mp4", 1))

    concat = work / "list.txt"
    concat.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    silent = work / "joined.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", concat.name, "-c", "copy", silent.name, cwd=work)

    total = sum(s.seconds for s in scenes) + end_seconds
    music = _pick_music(music_dir)
    fade = max(0.0, total - 1.5)
    inputs, labels, filters = ["-i", silent], [], []
    start = 0.0
    for sc in scenes:  # each scene's voice-over starts with its scene and never runs into the next one
        if sc.voice and Path(sc.voice).exists():
            idx = len(inputs) // 2
            inputs += ["-i", sc.voice]
            ms = int(start * 1000)
            filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{sc.seconds},"
                           f"adelay={ms}|{ms},volume=1.6[v{idx}]")
            labels.append(f"[v{idx}]")
        start += sc.seconds
    if music:
        idx = len(inputs) // 2
        inputs += ["-stream_loop", "-1", "-i", music]
        vol = 0.18 if labels else 0.6  # music sits under the voice
        filters.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,volume={vol},"
                       f"afade=t=in:d=0.5,afade=t=out:st={fade}:d=1.5[m]")
        labels.append("[m]")
    if labels:
        mix = "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,apad[a]"
        ffmpeg(*inputs, "-filter_complex", ";".join(filters + [mix]), "-map", "0:v", "-map", "[a]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-t", total, "-movflags", "+faststart", out)
    else:
        ffmpeg("-i", silent, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
               "-c:v", "copy", "-c:a", "aac", "-t", total, "-movflags", "+faststart", out)
    thumb = out.with_name("thumb.jpg")
    ffmpeg("-ss", "1", "-i", out, "-frames:v", "1", "-q:v", "3", thumb)
    return {"seconds": total, "music": music.name if music else None, "thumbnail": thumb,
            "voice": any(sc.voice for sc in scenes)}


def _transparent(path: Path) -> Path:
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(path)
    return path


def _pick_music(folder: Path | None):
    if not folder or not folder.exists():
        return None
    tracks = [p for p in folder.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac")]
    return random.choice(tracks) if tracks else None
