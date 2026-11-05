from datetime import timedelta
from itertools import groupby

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Role
from core.permissions import manager_required, staff_required
from inspections.models import TableInspection
from reservations.models import Reservation
from tasks.models import WaiterTask

from . import services
from .forms import AssignWaiterForm, BlockForm, FloorFilterForm, OverrideForm, TableForm
from .models import RestaurantTable, TableStatus


# --- Floor plan ------------------------------------------------------------------------


def floor_context(request):
    """Tables grouped by section, filtered by the query string. Shared by every dashboard."""
    form = FloorFilterForm(request.GET or None, restaurant=request.restaurant)
    tables = services.with_status(
        services.live_tables(request.restaurant).filter(is_active=True).order_by("section__sort_order", "number")
    )
    shown = form.filter(tables)
    sections = [(section, list(rows)) for section, rows in groupby(shown, key=lambda t: t.section)]
    return {
        "floor_form": form,
        "floor_sections": sections,
        "floor_count": len(shown),
        "floor_summary": services.floor_summary(tables),
        "floor_query": request.GET.urlencode(),
        "legend": [(value, label) for value, label in TableStatus.choices if value != TableStatus.INACTIVE],
    }


@staff_required
def floor(request):
    return render(request, "tables/floor.html", {"page_title": "Floor", **floor_context(request)})


@staff_required
def floor_fragment(request):
    return render(request, "tables/_floor.html", floor_context(request))


# --- One table ------------------------------------------------------------------------------


def live_table(request, pk):
    """A table of the user's restaurant with its live status, or 404."""
    table = get_object_or_404(services.live_tables(request.restaurant), pk=pk)
    return services.with_status([table])[0]


def detail_context(request, table):
    is_manager = request.role == Role.MANAGER
    context = {
        "table": table,
        "can_inspect": services.can_inspect(request.user, table),
        "inspections": TableInspection.objects.filter(table=table).select_related("inspector")[:10],
        "history": table.status_history.select_related("changed_by")[:15],
        "tasks": WaiterTask.objects.filter(table=table).select_related("assigned_to")[:10],
        "show_customers": request.role in (Role.MANAGER, Role.RECEPTIONIST),
        "bookings": Reservation.objects.filter(table=table, end_at__gte=timezone.now() - timedelta(hours=12))
        .exclude(status=Reservation.Status.CANCELLED)[:10],
        "page_title": f"Table {table.label}",
    }
    if is_manager:
        context.update(
            assign_form=AssignWaiterForm(initial={"waiter": table.assigned_waiter}, restaurant=request.restaurant),
            override_form=OverrideForm(initial={"is_occupied": "1" if table.is_occupied else "0",
                                                "cleanliness": table.cleanliness}),
            block_form=BlockForm(),
        )
    return context


@staff_required
def table_detail(request, pk):
    table = live_table(request, pk)
    return render(request, "tables/detail.html", detail_context(request, table))


@staff_required
def table_status_fragment(request, pk):
    table = live_table(request, pk)
    return render(request, "tables/_status_card.html", detail_context(request, table))


def redirect_after_action(request, table):
    return redirect(request.POST.get("next") or table.get_absolute_url())


def run_action(request, table, action, success_message):
    """Run a table service call and turn validation problems into a message."""
    try:
        action()
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, success_message)
    return redirect_after_action(request, table)


@manager_required
@require_POST
def assign_waiter(request, pk):
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant)
    form = AssignWaiterForm(request.POST, restaurant=request.restaurant)
    if not form.is_valid():
        messages.error(request, "Choose a waiter of this restaurant.")
        return redirect_after_action(request, table)
    waiter = form.cleaned_data["waiter"]
    return run_action(request, table, lambda: services.assign_waiter(table, waiter, user=request.user),
                      f"{table.label}: responsible waiter updated.")


@manager_required
@require_POST
def override(request, pk):
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant)
    form = OverrideForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Fill in every override field, including the reason.")
        return redirect_after_action(request, table)
    data = form.cleaned_data
    return run_action(request, table, lambda: services.override_state(
        table, is_occupied=data["is_occupied"], cleanliness=data["cleanliness"], reason=data["reason"], user=request.user,
    ), f"{table.label} overridden. The change is in the audit log.")


@manager_required
@require_POST
def block(request, pk):
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant)
    blocked = request.POST.get("blocked") == "1"
    return run_action(request, table, lambda: services.set_blocked(
        table, blocked=blocked, reason=request.POST.get("reason", ""), user=request.user,
    ), f"{table.label} {'blocked' if blocked else 'unblocked'}.")


@manager_required
@require_POST
def set_active(request, pk):
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant)
    active = request.POST.get("active") == "1"
    return run_action(request, table, lambda: services.set_active(table, active=active, user=request.user),
                      f"{table.label} {'reactivated' if active else 'deactivated'}.")


# --- Managing tables -----------------------------------------------------------------------------


@manager_required
def manage(request):
    tables = services.with_status(services.live_tables(request.restaurant).order_by("section__sort_order", "number"))
    return render(request, "tables/manage.html", {"tables": tables, "page_title": "Tables & QR codes"})


@manager_required
def table_edit(request, pk=None):
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant) if pk else None
    form = TableForm(request.POST or None, instance=table, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        table = form.save(commit=False)
        table.restaurant = request.restaurant
        table.save()
        messages.success(request, f"Table {table.label} saved.")
        return redirect(table)
    return render(request, "tables/form.html", {
        "form": form, "table": table, "page_title": f"Edit {table.label}" if table else "New table",
    })


# --- QR codes ----------------------------------------------------------------------------------------


@staff_required
def qr_png(request, pk):
    """QR image. Any staff member may view it; only managers may download it."""
    table = get_object_or_404(RestaurantTable, pk=pk, restaurant=request.restaurant)
    download = request.GET.get("download") == "1"
    if download and request.role != Role.MANAGER:
        raise PermissionDenied("Only managers can download QR codes.")
    response = HttpResponse(services.make_qr_png(table.qr_target_url), content_type="image/png")
    if download:
        response["Content-Disposition"] = f'attachment; filename="qr_{request.restaurant.slug}_T{table.number}.png"'
    response["Cache-Control"] = "private, max-age=300"
    return response


@manager_required
def qr_sheet(request):
    """Printable sheet: ?ids=1&ids=2 for selected tables, otherwise all active tables."""
    tables = RestaurantTable.objects.filter(restaurant=request.restaurant, is_active=True).select_related("section")
    ids = request.GET.getlist("ids")
    if ids:
        tables = tables.filter(pk__in=ids)
    return render(request, "tables/qr_sheet.html", {"labels": tables, "page_title": "Table QR codes"})


@staff_required
def qr_redirect(request, public_id):
    """
    Target of every table QR code (/t/<uuid>/). Requires a staff login of the
    same restaurant: staff who may inspect go straight to the inspection form,
    everyone else to the table page. Holding a QR code grants nothing.
    """
    table = get_object_or_404(RestaurantTable, public_id=public_id, restaurant=request.restaurant)
    if services.can_inspect(request.user, table):
        return redirect("inspections:inspect", pk=table.pk)
    return redirect(table)
