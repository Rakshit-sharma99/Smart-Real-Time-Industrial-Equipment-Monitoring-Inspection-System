from django.conf import settings

from accounts.models import Role

from .navigation import active_section, nav_items_for
from .permissions import get_role


def app_context(request):
    """Values every template needs: app name, the user's role and the sidebar."""
    role = get_role(getattr(request, "user", None))
    is_admin_user = role == Role.ADMIN
    return {
        "APP_NAME": settings.APP_NAME,
        "DEBUG": settings.DEBUG,
        "user_role": role,
        "user_role_label": Role(role).label if role else "",
        "is_admin_user": is_admin_user,
        "nav_items": nav_items_for(is_admin_user) if role else [],
        "nav_active": active_section(request),
        "offline_after_seconds": settings.OFFLINE_AFTER_SECONDS,
    }
