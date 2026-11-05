from django.contrib import admin
from django.urls import include, path

from core import pwa
from tables.views import qr_redirect

urlpatterns = [
    path("admin/", admin.site.urls),
    # PWA files must live at the site root (the service worker's scope is its own folder).
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("accounts/", include("accounts.urls")),
    # Short URL encoded in every table QR code. Requires a staff login, then redirects.
    path("t/<uuid:public_id>/", qr_redirect, name="table_qr_redirect"),
    path("floor/", include("tables.urls")),
    path("inspections/", include("inspections.urls")),
    path("tasks/", include("tasks.urls")),
    path("reservations/", include("reservations.urls")),
    path("notifications/", include("notifications.urls")),
    path("reports/", include("reports.urls")),
    path("api/v1/", include("config.api_urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"
