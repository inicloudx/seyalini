"""Reads a Google Play store listing: title, description, category, rating,
price notes and screenshots. Used by the brand strategist so the agent
"sees" the app the way a parent sees it in the store.

No API key needed. Google can change the page layout, so the parser tries
several sources (structured data, meta tags, visible HTML) and never crashes:
anything it can't find is simply left out.
"""
import html as htmllib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"


def package_id(url: str) -> str | None:
    if not url:
        return None
    q = parse_qs(urlparse(url).query)
    if q.get("id"):
        return q["id"][0]
    m = re.fullmatch(r"[a-zA-Z][\w]*(\.[\w]+)+", url.strip())
    return url.strip() if m else None


def fetch(url: str, timeout: float = 20) -> dict:
    pid = package_id(url)
    if not pid:
        raise ValueError("That doesn't look like a Google Play link (it needs ?id=com.your.app)")
    page = f"https://play.google.com/store/apps/details?id={pid}&hl=en&gl=IN"
    r = httpx.get(page, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    data = parse(r.text)
    data["package"] = pid
    data["url"] = f"https://play.google.com/store/apps/details?id={pid}"
    return data


def _clean(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", htmllib.unescape(s)).strip()


def _meta(page: str, key: str) -> str | None:
    for pat in (rf'<meta[^>]+(?:property|name|itemprop)="{re.escape(key)}"[^>]+content="([^"]*)"',
                rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name|itemprop)="{re.escape(key)}"'):
        m = re.search(pat, page, re.I)
        if m:
            return htmllib.unescape(m.group(1)).strip()
    return None


def parse(page: str) -> dict:
    out: dict = {}
    # 1. structured data (most reliable when present)
    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            ld = json.loads(block)
        except ValueError:
            continue
        for item in ld if isinstance(ld, list) else [ld]:
            if not isinstance(item, dict) or "SoftwareApplication" not in str(item.get("@type", "")):
                continue
            out.setdefault("title", item.get("name"))
            out.setdefault("description", _clean(item.get("description", "")))
            out.setdefault("category", item.get("applicationCategory"))
            out.setdefault("content_rating", item.get("contentRating"))
            author = item.get("author") or {}
            out.setdefault("developer", author.get("name") if isinstance(author, dict) else None)
            rating = item.get("aggregateRating") or {}
            if rating.get("ratingValue"):
                out["rating"] = f"{float(rating['ratingValue']):.1f} ({rating.get('ratingCount', '?')} ratings)"
            offers = item.get("offers") or []
            offers = offers if isinstance(offers, list) else [offers]
            if offers and str(offers[0].get("price", "")) not in ("", "0"):
                out["price"] = f"{offers[0].get('price')} {offers[0].get('priceCurrency', '')}".strip()
            if item.get("image"):
                out.setdefault("icon", item["image"])

    # 2. visible HTML / meta tags as fallback
    if not out.get("title"):
        m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        out["title"] = _clean(m.group(1)) if m else (_meta(page, "og:title") or "").replace(" - Apps on Google Play", "") or None
    full = re.search(r'<div[^>]+data-g-id="description"[^>]*>(.*?)</div>', page, re.S)
    if full and len(_clean(full.group(1))) > len(out.get("description") or ""):
        out["description"] = _clean(full.group(1))
    if not out.get("description"):
        out["description"] = _meta(page, "description") or _meta(page, "og:description")
    out.setdefault("icon", _meta(page, "og:image"))
    if re.search(r"In-app purchases", page):
        out["in_app_purchases"] = True
    if re.search(r"Contains ads", page):
        out["contains_ads"] = True
    m = re.search(r"Updated on</div><div[^>]*>([^<]+)</div>", page)
    if m:
        out["updated"] = m.group(1).strip()

    # 3. screenshots: images marked as screenshots, else large play-lh images
    shots = re.findall(r'<img[^>]+src="(https://play-lh\.googleusercontent\.com/[^"]+)"[^>]*alt="Screenshot image"', page)
    shots += re.findall(r'<img[^>]+alt="Screenshot image"[^>]*src="(https://play-lh\.googleusercontent\.com/[^"]+)"', page)
    if not shots:
        shots = re.findall(r'(https://play-lh\.googleusercontent\.com/[\w\-]+=w\d{3,4}-h\d{3,4})', page)
    seen, clean = set(), []
    for s in shots:
        base = s.split("=")[0]
        if base not in seen and base != (out.get("icon") or "").split("=")[0]:
            seen.add(base)
            clean.append(base)
    out["screenshots"] = clean[:8]
    return {k: v for k, v in out.items() if v not in (None, "", [])} | {"screenshots": clean[:8]}


def download_screenshots(urls: list[str], folder: Path, limit: int = 6) -> list[Path]:
    """Saves large versions (for videos) and returns their paths."""
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, base in enumerate(urls[:limit], 1):
        try:
            r = httpx.get(f"{base}=w1920", headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
            r.raise_for_status()
            path = folder / f"store_{i}.png"
            path.write_bytes(r.content)
            saved.append(path)
        except Exception:
            continue
    return saved
