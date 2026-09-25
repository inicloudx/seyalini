"""YouTube Data API v3 with plain HTTPS (no extra packages).

Flow: the owner connects their channel once (OAuth), we keep the refresh token
encrypted, and the Publisher swaps it for a short-lived access token per upload.
"""
from pathlib import Path
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


class YouTubeError(RuntimeError):
    pass


def _check(resp: httpx.Response) -> httpx.Response:
    if resp.status_code >= 400:
        try:
            err = resp.json().get("error", {})
            msg = err.get("message") if isinstance(err, dict) else f"{err}: {resp.json().get('error_description', '')}"
        except Exception:
            msg = resp.text[:300]
        raise YouTubeError(f"YouTube said {resp.status_code}: {msg}")
    return resp


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
    })


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    return _check(httpx.post(TOKEN_URL, data={
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code"}, timeout=30)).json()


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    return _check(httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token,
        "grant_type": "refresh_token"}, timeout=30)).json()["access_token"]


def my_channel(token: str) -> dict:
    items = _check(httpx.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                             headers={"Authorization": f"Bearer {token}"}, timeout=30)).json().get("items") or []
    if not items:
        raise YouTubeError("This Google account has no YouTube channel yet. Create one on youtube.com first.")
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def upload(token: str, video: Path, *, title: str, description: str, tags: list[str], privacy: str = "private",
           made_for_kids: bool = False, category_id: str = "27") -> str:
    """Resumable upload; returns the new video id. Category 27 = Education."""
    meta = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30], "categoryId": category_id},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids, "embeddable": True},
    }
    size = video.stat().st_size
    init = _check(httpx.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=meta, timeout=60,
                             headers={"Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
                                      "X-Upload-Content-Length": str(size)}))
    location = init.headers.get("Location")
    if not location:
        raise YouTubeError("YouTube did not return an upload address")
    with video.open("rb") as fh:
        done = _check(httpx.put(location, content=fh.read(), timeout=600,
                                headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"}))
    return done.json()["id"]
