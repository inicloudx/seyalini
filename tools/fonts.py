"""Finds a good bold font on Windows, Mac or Linux. A brand font can be dropped
into tenants/<org>/products/<app>/assets/fonts/ and is used first."""
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

CANDIDATES = {
    True: [
        "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ],
    False: [
        "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ],
}

_brand_fonts: list[Path] = []


def use_brand_fonts(folder: Path | None):
    """Call with the product's assets/fonts folder before rendering."""
    _brand_fonts.clear()
    if folder and folder.exists():
        _brand_fonts.extend(sorted(folder.glob("*.ttf")) + sorted(folder.glob("*.otf")))
    font.cache_clear()


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    brand = [p for p in _brand_fonts if ("bold" in p.name.lower()) == bold] or _brand_fonts
    for path in [str(p) for p in brand] + CANDIDATES[bold]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)
