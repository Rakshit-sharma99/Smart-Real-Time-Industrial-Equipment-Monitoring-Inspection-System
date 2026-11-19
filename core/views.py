from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseServerError
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader

from accounts.models import Role
from tables.models import RestaurantSection

from . import services
from .forms import RestaurantForm, SectionForm
from .floor import floor_screen, live_context
from .permissions import booking_staff_required, manager_required, staff_required


@staff_required
def home(request):
    """Each role lands on its own dashboard."""
    if request.role == Role.WAITER:
        return redirect("tasks:waiter_home")
    if request.role == Role.RECEPTIONIST:
        return redirect("reservations:desk")
    return redirect("core:manager_dashboard")


# --- Floor (manager dashboard and booking desk are the same screen) -----------------------


@manager_required
def manager_dashboard(request):
    return floor_screen(request)


@booking_staff_required
def fragment_overview(request):
    services.run_periodic_checks(request.restaurant)
    return render(request, "core/_floor_live.html", live_context(request))


# --- Settings -------------------------------------------------------------------------------


@staff_required
def settings_home(request):
    """Managers land on the restaurant settings; other staff on their own profile."""
    if request.role == Role.MANAGER:
        return redirect("core:restaurant_settings")
    return redirect("accounts:profile")


@manager_required
def restaurant_settings(request):
    form = RestaurantForm(request.POST or None, instance=request.restaurant)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Restaurant settings saved.")
        return redirect("core:restaurant_settings")
    return render(request, "core/restaurant_settings.html",
                  {"form": form, "page_title": "Restaurant settings", "settings_tab": "restaurant"})


@manager_required
def sections(request):
    rows = RestaurantSection.objects.filter(restaurant=request.restaurant)
    return render(request, "core/sections.html",
                  {"sections": rows, "page_title": "Sections", "settings_tab": "sections"})


@manager_required
def section_edit(request, pk=None):
    section = get_object_or_404(RestaurantSection, pk=pk, restaurant=request.restaurant) if pk else None
    form = SectionForm(request.POST or None, instance=section, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        section = form.save(commit=False)
        section.restaurant = request.restaurant
        section.save()
        messages.success(request, f"Section '{section.name}' saved.")
        return redirect("core:sections")
    return render(request, "core/section_form.html", {
        "form": form, "section": section, "settings_tab": "sections",
        "page_title": f"Edit {section.name}" if section else "New section",
    })


# --- Error pages ---------------------------------------------------------------------------------


def permission_denied(request, exception=None):
    reason = str(exception) if isinstance(exception, PermissionDenied) and str(exception) else ""
    return render(request, "403.html", {"page_title": "Access denied", "reason": reason}, status=403)


def page_not_found(request, exception=None):
    return render(request, "404.html", {"page_title": "Not found"}, status=404)


def server_error(request):
    # Rendered without context processors: if something is broken we don't
    # want the error page itself to fail.
    return HttpResponseServerError(loader.get_template("500.html").render())
