"""
Progressive Web App files: the manifest and the service worker.

The service worker is served from the site root (/sw.js) so its scope covers
the whole app. It caches ONLY /static/ files (cache-first) plus an offline
page. Pages and /api/ calls always go to the network, so logged-in HTML and
JSON are never stored on the device.
"""
import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.templatetags.static import static


def static_version():
    """Changes whenever any file in static/ changes, so browsers pick up new CSS/JS."""
    latest = 0
    for folder in settings.STATICFILES_DIRS:
        for path in Path(folder).rglob("*"):
            if path.is_file():
                latest = max(latest, int(path.stat().st_mtime))
    return str(latest)


@login_not_required
def manifest(request):
    data = {
        "name": settings.APP_NAME,
        "short_name": settings.APP_NAME,
        "description": "Equipment monitoring and inspection (simulated sensor data).",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f6f7f9",
        "theme_color": "#1f2328",
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return HttpResponse(json.dumps(data, indent=2), content_type="application/manifest+json")


@login_not_required
def service_worker(request):
    script = render_to_string(
        "pwa/sw.js",
        {"cache_name": f"equipwatch-static-{static_version()}", "static_url": settings.STATIC_URL,
         "offline_url": static("offline.html")},
    )
    response = HttpResponse(script, content_type="application/javascript")
    response["Cache-Control"] = "no-cache"  # always check for a new worker
    return response
