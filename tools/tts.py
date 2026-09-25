"""Voice-over with Gemini text-to-speech (Interactions API, google-genai 2.25+).

About $0.004 for a 25-second Short with the Lite voice model. Returns a WAV file.
"""
import base64
import wave
from pathlib import Path

DEFAULT_MODEL = "gemini-3.8-flash-lite-tts"
RATE = 24000


def _audio_bytes(interaction) -> bytes:
    audio = getattr(interaction, "output_audio", None)
    data = getattr(audio, "data", None) if audio is not None else None
    if data is None:  # some SDK versions put it in the outputs list
        for item in getattr(interaction, "outputs", None) or []:
            data = getattr(item, "data", None) or (item.get("data") if isinstance(item, dict) else None)
            if data:
                break
    if not data:
        raise RuntimeError("The voice model returned no audio")
    return base64.b64decode(data) if isinstance(data, str) else bytes(data)


def generate(text: str, out: Path, *, api_key: str, model: str = DEFAULT_MODEL, voice: str = "Kore",
             style: str = "warm, cheerful and clear, like a friendly teacher talking to young children") -> Path:
    from .genai_client import client

    c = client(api_key)
    if not hasattr(c, "interactions"):
        raise RuntimeError("Voice-over needs a newer google-genai: run  pip install -U google-genai")
    interaction = c.interactions.create(
        model=model,
        input=[{"type": "user_input", "content": [{
            "type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        response_format={"type": "audio", "mime_type": "audio/l16", "sample_rate": RATE},
        generation_config={"speech_config": [{"voice": voice}]},
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:  # raw 16-bit PCM -> WAV
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(_audio_bytes(interaction))
    return out


def silence_placeholder(out: Path, seconds: float = 1.0) -> Path:
    """Dry run: a short tone-free WAV so the editor path is exercised for free."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * int(RATE * seconds))
    return out
