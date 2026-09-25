"""Makes Seyalini installable on a phone ("Add to Home screen") - the first step to the mobile app."""
from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static


def manifest(request):
    return JsonResponse({
        "name": "Seyalini", "short_name": "Seyalini", "description": "Your AI marketing team",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#17171A", "theme_color": "#17171A",
        "icons": [{"src": static("dashboard/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                  {"src": static("dashboard/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
    }, content_type="application/manifest+json")


SW = """// Seyalini service worker: always fetch fresh data (the dashboard is live), just makes the app installable.
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', () => {});
"""


def service_worker(request):
    return HttpResponse(SW, content_type="application/javascript")
