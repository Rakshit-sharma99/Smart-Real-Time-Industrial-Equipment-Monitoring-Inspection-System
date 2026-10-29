from django.http import HttpResponseServerError
from django.shortcuts import redirect, render
from django.template import loader

from accounts.models import Role
from equipment.models import Equipment
from monitoring.models import Alert
from monitoring.services import latest_readings

from .permissions import get_role
from .services import dashboard_summary


def dashboard(request):
    machines = list(Equipment.objects.active())
    latest = latest_readings(machines)
    context = {
        "page_title": "Overview",
        "summary": dashboard_summary(request.user),
        "equipment_rows": [{"equipment": eq, "reading": latest.get(eq.pk)} for eq in machines],
        "recent_alerts": Alert.objects.select_related("equipment")[:6],
        "trend_machines": machines,
    }
    return render(request, "core/dashboard.html", context)


def settings_home(request):
    """Admins land on the simulator settings; inspectors on their own profile."""
    if get_role(request.user) == Role.ADMIN:
        return redirect("monitoring:simulator_settings")
    return redirect("accounts:profile")


# --- Error pages ---------------------------------------------------------------


def permission_denied(request, exception=None):
    return render(request, "403.html", {"page_title": "Access denied"}, status=403)


def page_not_found(request, exception=None):
    return render(request, "404.html", {"page_title": "Not found"}, status=404)


def server_error(request):
    # Rendered without context processors: if something is broken we don't
    # want the error page itself to fail.
    return HttpResponseServerError(loader.get_template("500.html").render())
