from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from accounts.models import Role
from core.permissions import RoleRequiredMixin, floor_staff_required
from tables import services as table_services

from . import services
from .forms import InspectionFilterForm, InspectionForm, initial_from_table
from .models import TableInspection, TableInspectionPhoto


class InspectionListView(RoleRequiredMixin, ListView):
    """Managers: every inspection of the restaurant. Waiters: their own."""

    allowed_roles = (Role.MANAGER, Role.WAITER)
    template_name = "inspections/list.html"
    context_object_name = "inspections"
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = InspectionFilterForm(self.request.GET or None)
        visible = TableInspection.objects.visible_to(self.request.user, self.request.restaurant)
        return self.filter_form.filter(visible.select_related("table", "inspector"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["page_title"] = "Table checks" if self.request.role == Role.MANAGER else "My table checks"
        return context


def visible_inspection(request, pk):
    queryset = TableInspection.objects.visible_to(request.user, request.restaurant)
    return get_object_or_404(queryset.select_related("table", "inspector", "task"), pk=pk)


@floor_staff_required
def inspection_detail(request, pk):
    inspection = visible_inspection(request, pk)
    return render(request, "inspections/detail.html", {
        "inspection": inspection, "photos": inspection.photos.all(),
        "page_title": f"{inspection.reference} · {inspection.table.label}",
    })


@floor_staff_required
def inspect(request, pk):
    """The mobile inspection form for one table (QR codes lead here)."""
    table = get_object_or_404(table_services.live_tables(request.restaurant), pk=pk)
    table = table_services.with_status([table])[0]
    if not table_services.can_inspect(request.user, table):
        raise PermissionDenied("This table is assigned to another waiter. Ask your manager if it should be yours.")
    form = InspectionForm(request.POST or None, request.FILES or None, initial=initial_from_table(table))
    if request.method == "POST" and form.is_valid():
        try:
            inspection = services.record_inspection(
                table, request.user, remarks=form.cleaned_data["remarks"], photos=form.cleaned_data["photos"],
                **form.answers(),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"{table.label} checked: {inspection.get_resulting_status_display()}.")
            return redirect(table)
    return render(request, "inspections/inspect.html", {
        "form": form, "table": table, "page_title": f"Check {table.label}",
    })


@floor_staff_required
def photo(request, pk):
    """Inspection photos are served only to staff who may see the inspection - never from a public URL."""
    item = get_object_or_404(
        TableInspectionPhoto.objects.filter(
            inspection__in=TableInspection.objects.visible_to(request.user, request.restaurant)
        ),
        pk=pk,
    )
    try:
        file = item.image.open("rb")
    except FileNotFoundError:
        raise Http404("Photo file is missing.")
    response = FileResponse(file, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@floor_staff_required
def scanner(request):
    """Scan a table's QR code (camera, image file) or type its number."""
    return render(request, "inspections/scanner.html", {"page_title": "Scan table QR"})
