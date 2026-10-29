from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from core.permissions import RoleRequiredMixin, admin_required, is_admin
from equipment.models import Equipment

from . import services
from .forms import (
    AssignInspectionForm,
    ChecklistItemFormSet,
    DefectFilterForm,
    InspectionFilterForm,
    InspectionRemarksForm,
    ResponseFormSet,
    TemplateForm,
)
from .models import Defect, Inspection, InspectionResponse, InspectionTemplate


def visible_inspection(request, pk):
    """Fetch an inspection the user may see, or 404 (never reveal that it exists)."""
    queryset = Inspection.objects.visible_to(request.user).select_related(
        "equipment", "template", "inspector", "created_by"
    )
    return get_object_or_404(queryset, pk=pk)


# --- Lists ---------------------------------------------------------------------------


class InspectionListView(RoleRequiredMixin, ListView):
    template_name = "inspections/list.html"
    context_object_name = "inspections"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = InspectionFilterForm(self.request.GET or {"status": "open"})
        visible = Inspection.objects.visible_to(self.request.user).select_related("equipment", "inspector")
        return self.filter_form.filter(visible)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(filter_form=self.filter_form, page_title="Inspections", inspections_tab="inspections")
        return context


class DefectListView(RoleRequiredMixin, ListView):
    template_name = "inspections/defects.html"
    context_object_name = "defects"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = DefectFilterForm(self.request.GET or {"status": "unresolved"})
        visible = Defect.objects.visible_to(self.request.user).select_related("equipment", "inspection")
        return self.filter_form.filter(visible)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(filter_form=self.filter_form, page_title="Defects", inspections_tab="defects")
        return context


# --- One inspection --------------------------------------------------------------------


def inspection_detail(request, pk):
    inspection = visible_inspection(request, pk)
    context = {
        "inspection": inspection,
        "responses": inspection.responses.all(),
        "defects": inspection.defects.all(),
        "counts": inspection.outcome_counts(),
        "is_assignee": inspection.inspector_id == request.user.pk,
        "page_title": f"{inspection.reference} · {inspection.equipment.equipment_code}",
    }
    return render(request, "inspections/detail.html", context)


@require_POST
def inspection_start(request, pk):
    inspection = visible_inspection(request, pk)
    try:
        services.start_inspection(inspection, request.user)
    except services.InspectionError as exc:
        messages.error(request, str(exc))
        return redirect("inspections:detail", pk=pk)
    return redirect("inspections:perform", pk=pk)


def inspection_perform(request, pk):
    """Fill in the checklist: 'Save progress' keeps it IN_PROGRESS, 'Submit' finishes it."""
    inspection = visible_inspection(request, pk)
    if inspection.inspector_id != request.user.pk:
        raise PermissionDenied("Only the assigned inspector can fill in this inspection.")
    if inspection.status != Inspection.Status.IN_PROGRESS:
        if inspection.is_submitted:
            messages.info(request, "This inspection is submitted and can no longer be changed.")
        return redirect("inspections:detail", pk=pk)

    formset = ResponseFormSet(
        request.POST or None, request.FILES or None, queryset=inspection.responses.all()
    )
    remarks_form = InspectionRemarksForm(request.POST or None, instance=inspection)
    submit_errors = []

    if request.method == "POST" and formset.is_valid() and remarks_form.is_valid():
        try:
            services.save_progress(inspection, request.user, formset, remarks_form)
        except services.InspectionError as exc:
            messages.error(request, str(exc))
            return redirect("inspections:detail", pk=pk)

        if request.POST.get("action") != "submit":
            messages.success(request, "Progress saved. You can come back and finish later.")
            return redirect("inspections:perform", pk=pk)
        try:
            services.submit_inspection(inspection, request.user)
        except ValidationError as exc:
            submit_errors = exc.messages
        except services.InspectionError as exc:
            messages.error(request, str(exc))
            return redirect("inspections:detail", pk=pk)
        else:
            inspection.refresh_from_db()
            messages.success(
                request, f"{inspection.reference} submitted. Result: {inspection.get_overall_result_display()}."
            )
            return redirect("inspections:detail", pk=pk)
        # Saved, but not submittable yet: show the form again with the problems.
        formset = ResponseFormSet(queryset=inspection.responses.all())
        remarks_form = InspectionRemarksForm(instance=inspection)

    context = {
        "inspection": inspection,
        "formset": formset,
        "remarks_form": remarks_form,
        "submit_errors": submit_errors,
        "page_title": f"Inspect {inspection.equipment.equipment_code}",
    }
    return render(request, "inspections/perform.html", context)


def response_photo(request, pk):
    """Serve an inspection photo after checking the user may see that inspection."""
    response = get_object_or_404(
        InspectionResponse.objects.filter(inspection__in=Inspection.objects.visible_to(request.user)),
        pk=pk,
    )
    if not response.photo:
        raise Http404("No photo.")
    try:
        file = response.photo.open("rb")
    except FileNotFoundError:
        raise Http404("Photo file is missing.")
    result = FileResponse(file, content_type="image/jpeg")
    result["Cache-Control"] = "private, max-age=3600"
    result["X-Content-Type-Options"] = "nosniff"
    return result


# --- Creating inspections ------------------------------------------------------------------


@admin_required
def inspection_assign(request):
    initial = {}
    code = request.GET.get("equipment")
    if code:
        equipment = Equipment.objects.active().filter(equipment_code=code).first()
        if equipment:
            initial["equipment"] = equipment
            initial["template"] = InspectionTemplate.objects.template_for(equipment)
    form = AssignInspectionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            inspection = services.assign_inspection(created_by=request.user, **form.cleaned_data)
        except services.InspectionError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(
                request,
                f"{inspection.reference} assigned to {inspection.inspector.get_full_name() or inspection.inspector.username}.",
            )
            return redirect("inspections:detail", pk=inspection.pk)
    return render(request, "inspections/assign.html", {"form": form, "page_title": "Assign inspection"})


@require_POST
def inspection_start_adhoc(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    try:
        inspection = services.start_adhoc_inspection(equipment, request.user)
    except services.InspectionError as exc:
        messages.error(request, str(exc))
        return redirect(equipment)
    messages.success(request, f"{inspection.reference} started for {equipment.equipment_code}.")
    return redirect("inspections:perform", pk=inspection.pk)


# --- Checklist templates (admin, under Settings) ------------------------------------------------


@admin_required
def template_list(request):
    templates = InspectionTemplate.objects.all()
    return render(
        request,
        "inspections/template_list.html",
        {"templates": templates, "page_title": "Checklist templates", "settings_tab": "templates"},
    )


@admin_required
def template_edit(request, pk=None):
    template = get_object_or_404(InspectionTemplate, pk=pk) if pk else None
    form = TemplateForm(request.POST or None, instance=template)
    formset = ChecklistItemFormSet(request.POST or None, instance=template or InspectionTemplate())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            template = form.save()
            formset.instance = template
            formset.save()
        messages.success(request, f"Template '{template.name}' saved. Inspections already started keep their own copy.")
        return redirect("inspections:template_list")
    context = {
        "form": form,
        "formset": formset,
        "template_obj": template,
        "page_title": f"Edit {template.name}" if template else "New checklist template",
        "settings_tab": "templates",
    }
    return render(request, "inspections/template_form.html", context)


def can_start_adhoc(equipment, user):
    """Used by the equipment page to decide which inspection button to show."""
    return services.adhoc_blocker(equipment, user) is None and not is_admin(user)
