from django.contrib import admin
from django.urls import include, path

from core import pwa
from equipment.views import qr_redirect

urlpatterns = [
    path("admin/", admin.site.urls),
    # PWA files must live at the site root (the service worker's scope is its own folder).
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("accounts/", include("accounts.urls")),
    path("equipment/", include("equipment.urls")),
    # Short URL encoded in every QR label. Requires login, then redirects.
    path("e/<uuid:public_id>/", qr_redirect, name="equipment_qr_redirect"),
    path("alerts/", include("monitoring.alert_urls")),
    path("inspections/", include("inspections.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("reports/", include("reports.urls")),
    path("api/v1/", include("config.api_urls")),
    path("", include("monitoring.urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"
