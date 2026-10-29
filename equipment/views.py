from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from core.permissions import RoleRequiredMixin, admin_required, is_admin
from inspections import services as inspection_services
from inspections.models import Defect, Inspection
from maintenance.models import MaintenanceTask

from . import services
from .forms import EquipmentCreateForm, EquipmentDetailsForm, EquipmentFilterForm, ThresholdForm
from .models import Equipment


class EquipmentListView(RoleRequiredMixin, ListView):
    template_name = "equipment/list.html"
    context_object_name = "equipment_list"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = EquipmentFilterForm(self.request.GET or None)
        return self.filter_form.filter(Equipment.objects.all())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["page_title"] = "Equipment"
        return context


def equipment_detail(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    readings = equipment.readings.order_by("-recorded_at", "-id")
    latest = readings.first()
    context = {
        "equipment": equipment,
        "latest_reading": latest,
        "metric_cards": services.metric_rows(equipment, latest),
        "recent_readings": readings[:20],
        "alerts": equipment.alerts.select_related("equipment")[:20],
        "inspections": Inspection.objects.visible_to(request.user)
        .filter(equipment=equipment)
        .select_related("equipment", "inspector")[:10],
        "defects": Defect.objects.visible_to(request.user)
        .filter(equipment=equipment)
        .select_related("equipment", "inspection")[:10],
        "open_assignment": Inspection.objects.open().filter(equipment=equipment, inspector=request.user).first(),
        "can_start_adhoc": inspection_services.adhoc_blocker(equipment, request.user) is None,
        "tasks": MaintenanceTask.objects.visible_to(request.user)
        .filter(equipment=equipment)
        .select_related("equipment", "assigned_to")[:10],
        "page_title": equipment.equipment_code,
    }
    return render(request, "equipment/detail.html", context)


@admin_required
def equipment_create(request):
    form = EquipmentCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        equipment = form.save()
        messages.success(request, f"{equipment.equipment_code} created. Its QR label is ready to print.")
        return redirect(equipment)
    return render(request, "equipment/form.html", {"form": form, "page_title": "Add equipment"})


@admin_required
def equipment_edit(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    form = EquipmentDetailsForm(request.POST or None, instance=equipment)
    if request.method == "POST" and form.is_valid():
        equipment = form.save()
        messages.success(request, f"{equipment.equipment_code} updated.")
        return redirect(equipment)
    return render(
        request,
        "equipment/form.html",
        {"form": form, "equipment": equipment, "page_title": f"Edit {equipment.equipment_code}"},
    )


@admin_required
def equipment_thresholds(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    form = ThresholdForm(request.POST or None, instance=equipment)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Thresholds for {equipment.equipment_code} saved.")
        return redirect(equipment)
    return render(
        request,
        "equipment/thresholds.html",
        {"form": form, "equipment": equipment, "page_title": f"Thresholds · {equipment.equipment_code}"},
    )


@admin_required
@require_POST
def equipment_set_active(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    activate = request.POST.get("active") == "1"
    services.set_active(equipment, activate)
    messages.success(
        request, f"{equipment.equipment_code} {'reactivated' if activate else 'deactivated'}."
    )
    return redirect(equipment)


# --- QR codes ---------------------------------------------------------------------


def equipment_qr_png(request, code):
    """QR image. Everyone may view it inline; only admins may download it."""
    equipment = get_object_or_404(Equipment, equipment_code=code)
    download = request.GET.get("download") == "1"
    if download and not is_admin(request.user):
        raise PermissionDenied("Only admins can download QR codes.")
    response = HttpResponse(services.make_qr_png(equipment.qr_target_url), content_type="image/png")
    if download:
        response["Content-Disposition"] = f'attachment; filename="qr_{slugify(equipment.equipment_code)}.png"'
    response["Cache-Control"] = "private, max-age=300"
    return response


@admin_required
def equipment_label(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    return render(request, "equipment/labels.html", {"labels": [equipment], "page_title": "Print label"})


@admin_required
def equipment_label_sheet(request):
    """Printable sheet for the equipment ticked on the list page (?codes=EQ-001&codes=EQ-002)."""
    codes = request.GET.getlist("codes")
    labels = list(Equipment.objects.filter(equipment_code__in=codes))
    if not labels:
        messages.warning(request, "Tick at least one machine to print labels.")
        return redirect("equipment:list")
    return render(request, "equipment/labels.html", {"labels": labels, "page_title": "Print labels"})


def qr_redirect(request, public_id):
    """Target of every QR code: /e/<uuid>/ -> the equipment page (login required)."""
    equipment = get_object_or_404(Equipment, public_id=public_id)
    return redirect(equipment)
