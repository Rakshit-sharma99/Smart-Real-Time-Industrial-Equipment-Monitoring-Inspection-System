"""Alert pages: list, detail, acknowledge (any role) and resolve (admin)."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from core.permissions import RoleRequiredMixin, admin_required

from . import services
from .forms import AlertFilterForm, ResolveAlertForm
from .models import Alert


class AlertListView(RoleRequiredMixin, ListView):
    template_name = "alerts/list.html"
    context_object_name = "alerts"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = AlertFilterForm(self.request.GET or {"status": "open"})
        return self.filter_form.filter(Alert.objects.select_related("equipment"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["page_title"] = "Alerts"
        return context


def alert_detail(request, pk):
    alert = get_object_or_404(
        Alert.objects.select_related("equipment", "acknowledged_by", "resolved_by"), pk=pk
    )
    context = {
        "alert": alert,
        "resolve_form": ResolveAlertForm(),
        "page_title": f"{alert.reference} · {alert.equipment.equipment_code}",
    }
    return render(request, "alerts/detail.html", context)


def redirect_back(request, alert):
    """Return to the page the action came from (e.g. the list), else the alert page."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect("alerts:detail", pk=alert.pk)


@require_POST
def alert_acknowledge(request, pk):
    alert = get_object_or_404(Alert, pk=pk)
    try:
        services.acknowledge_alert(alert, request.user)
        messages.success(request, f"{alert.reference} acknowledged.")
    except services.AlertActionError as exc:
        messages.error(request, str(exc))
    return redirect_back(request, alert)


@admin_required
@require_POST
def alert_resolve(request, pk):
    alert = get_object_or_404(Alert.objects.select_related("equipment"), pk=pk)
    form = ResolveAlertForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Resolution notes are required to resolve an alert.")
        return render(
            request,
            "alerts/detail.html",
            {"alert": alert, "resolve_form": form, "page_title": f"{alert.reference} · {alert.equipment.equipment_code}"},
            status=400,
        )
    try:
        services.resolve_alert(alert, request.user, form.cleaned_data["resolution_notes"])
        messages.success(request, f"{alert.reference} resolved.")
    except (services.AlertActionError, ValidationError) as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    return redirect("alerts:detail", pk=alert.pk)
