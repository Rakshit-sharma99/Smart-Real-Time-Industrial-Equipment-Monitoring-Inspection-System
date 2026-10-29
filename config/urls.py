from django.contrib import admin
from django.urls import include, path

from equipment.views import qr_redirect

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("equipment/", include("equipment.urls")),
    # Short URL encoded in every QR label. Requires login, then redirects.
    path("e/<uuid:public_id>/", qr_redirect, name="equipment_qr_redirect"),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
handler404 = "core.views.page_not_found"
handler500 = "core.views.server_error"
