"""
The Floor screen: one floor view for managers and receptionists.

Top: four state counts that filter the floor. Left: the person's to-do list
(managers: "Needs you now"; receptionists: guests arriving and seated).
Right: the floor, with a tab per section. "+ Walk-in" and "+ Booking" open
side drawers, so the old Booking desk and Overview are the same screen.
"""
from django.shortcuts import render

from accounts.models import Role
from tables import services as table_services
from tables.forms import waiters_of
from tables.models import DisplayState, RestaurantSection

from . import services


def live_context(request):
    """The part that refreshes live: counts, to-do list and floor."""
    restaurant = request.restaurant
    tables = table_services.with_status(
        table_services.live_tables(restaurant).filter(is_active=True).order_by("section__sort_order", "number")
    )
    context = {
        "stats": services.overview_stats(tables),
        "floor_tables": tables,
        "floor_sections": list(RestaurantSection.objects.filter(restaurant=restaurant)),
        "display_states": DisplayState.choices,
    }
    if request.role == Role.MANAGER:
        context["todo"] = services.attention_items(restaurant, tables)
    else:
        from reservations.views import upcoming_context
        context.update(upcoming_context(request))
    return context


def floor_screen(request, booking_form=None, status=200):
    """Render the whole Floor screen (drawers included) for the current role."""
    from reservations.views import desk_context

    services.run_periodic_checks(request.restaurant)
    context = {
        "page_title": "Floor",
        **desk_context(request, booking_form=booking_form),
        **live_context(request),
        # Open a drawer straight away after a search or a failed booking.
        "open_drawer": "booking" if (booking_form is not None or "guests" in request.GET) else request.GET.get("drawer", ""),
    }
    if request.role == Role.MANAGER:
        context["waiters"] = waiters_of(request.restaurant)
    return render(request, "core/floor.html", context, status=status)
