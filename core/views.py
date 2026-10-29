from django.http import HttpResponseServerError
from django.shortcuts import redirect, render
from django.template import loader

from accounts.models import Role
from equipment.models import Equipment
from equipment.services import metric_rows
from inspections.models import Inspection
from monitoring.models import Alert
from monitoring.services import latest_readings

from .permissions import get_role
from .services import dashboard_summary, task_summary


def dashboard(request):
    machines = list(Equipment.objects.active())
    latest = latest_readings(machines)
    summary = dashboard_summary(request.user)
    context = {
        "page_title": "Overview",
        "summary": summary,
        "dashboard_data": {"summary": summary},
        "equipment_rows": [
            {"equipment": eq, "metrics": metric_rows(eq, latest.get(eq.pk))} for eq in machines
        ],
        "recent_alerts": Alert.objects.select_related("equipment")[:6],
        "recent_inspections": Inspection.objects.visible_to(request.user).select_related("equipment", "inspector")[:5],
        "trend_machines": machines,
    }
    context["task_rows"], context["overdue_tasks"] = task_summary(request.user)
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
