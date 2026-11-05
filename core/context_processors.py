from django.conf import settings

from accounts.models import Role
from notifications.models import Notification

from .navigation import active_section, nav_items_for


def app_context(request):
    """Values every template needs: app name, role flags, the sidebar and the notification bell."""
    role = getattr(request, "role", None)
    user = getattr(request, "user", None)
    context = {
        "APP_NAME": settings.APP_NAME,
        "DEBUG": settings.DEBUG,
        "user_role": role,
        "user_role_label": Role(role).label if role else "",
        "is_manager_user": role == Role.MANAGER,
        "is_waiter_user": role == Role.WAITER,
        "is_receptionist_user": role == Role.RECEPTIONIST,
        "nav_items": nav_items_for(role) if role else [],
        "nav_active": active_section(request),
    }
    if role and user is not None:
        inbox = Notification.objects.filter(recipient=user)
        context["unread_notifications"] = inbox.filter(is_read=False).count()
        context["latest_notifications"] = inbox[:8]
    return context
