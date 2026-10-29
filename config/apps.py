from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


class SuperuserAdminSite(AdminSite):
    """Django admin restricted to superusers (staff flag alone is not enough)."""

    site_header = "EquipWatch administration"
    site_title = "EquipWatch admin"

    def has_permission(self, request):
        return request.user.is_active and request.user.is_superuser


class SuperuserAdminConfig(AdminConfig):
    default_site = "config.apps.SuperuserAdminSite"
