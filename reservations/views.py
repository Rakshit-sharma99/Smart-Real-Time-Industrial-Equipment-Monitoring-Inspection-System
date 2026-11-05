from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.permissions import booking_staff_required
from tables.views import floor_context

from . import services
from .forms import BookingForm, BookingSearchForm, ReservationFilterForm, WalkInForm
from .models import Reservation

Status = Reservation.Status


# --- Booking desk ----------------------------------------------------------------------------


def upcoming_context(request):
    """Guests expected in the next hours (plus late ones) and tables currently seated from a booking."""
    now = timezone.now()
    bookings = Reservation.objects.for_restaurant(request.restaurant).select_related("table")
    return {
        "upcoming": bookings.filter(status__in=Reservation.WAITING_STATUSES, end_at__gt=now,
                                    start_at__lte=now + timedelta(hours=6)).order_by("start_at"),
        "seated": bookings.filter(status=Status.SEATED).order_by("seated_at"),
        "later_count": bookings.filter(status__in=Reservation.WAITING_STATUSES, start_at__gt=now + timedelta(hours=6)).count(),
    }


def search_context(request, form):
    """Suitable tables for the desk's search (date, time, guests, section)."""
    if not form.is_valid():
        return {"suggestions": None}
    data = form.cleaned_data
    end = services.end_for(request.restaurant, data["start_at"], data.get("duration"))
    suggestions = services.available_tables(
        request.restaurant, start=data["start_at"], end=end, party_size=data["guests"], section=data.get("section"),
    )
    return {"suggestions": suggestions, "search_end": end}


def desk_context(request, booking_form=None, walk_in_form=None):
    now = timezone.localtime()
    search_data = request.POST if booking_form is not None else (request.GET if "guests" in request.GET else None)
    search = BookingSearchForm(search_data, restaurant=request.restaurant, initial={
        "date": now.date(), "time": (now + timedelta(minutes=30)).strftime("%H:00"), "guests": 2,
    })
    ready_now = services.available_tables(
        request.restaurant, start=timezone.now(), end=services.end_for(request.restaurant, timezone.now()),
        party_size=1, walk_in=True,
    )
    return {
        "search_form": search,
        "booking_form": booking_form or BookingForm(restaurant=request.restaurant, initial=search.initial),
        "walk_in_form": walk_in_form or WalkInForm(restaurant=request.restaurant, ready_tables=ready_now),
        "ready_now": ready_now,
        **search_context(request, search),
        **upcoming_context(request),
        **floor_context(request),
    }


@booking_staff_required
def desk(request):
    return render(request, "reservations/desk.html", {"page_title": "Booking desk", **desk_context(request)})


@booking_staff_required
def desk_board_fragment(request):
    return render(request, "reservations/_desk_board.html", floor_context(request))


@booking_staff_required
def upcoming_fragment(request):
    return render(request, "reservations/_upcoming.html", upcoming_context(request))


@booking_staff_required
@require_POST
def book(request):
    form = BookingForm(request.POST, restaurant=request.restaurant)
    if form.is_valid():
        data = form.cleaned_data
        try:
            reservation = services.create_reservation(
                restaurant=request.restaurant, table=data["table"], customer_name=data["customer_name"],
                phone=data["phone"], party_size=data["guests"], start_at=data["start_at"],
                duration_minutes=data.get("duration"), remarks=data["remarks"], created_by=request.user,
            )
        except ValidationError as exc:
            for field, problems in exc.message_dict.items():
                form.add_error(field if field in form.fields else None, problems)
        else:
            messages.success(request, f"Booked {reservation.reference}: {reservation.table.label} for "
                                      f"{reservation.customer_name}, {timezone.localtime(reservation.start_at):%d %b %H:%M}.")
            return redirect("reservations:desk")
    context = {"page_title": "Booking desk", **desk_context(request, booking_form=form)}
    return render(request, "reservations/desk.html", context, status=400)


@booking_staff_required
@require_POST
def walk_in(request):
    # The service re-checks that the chosen table is Ready right now.
    form = WalkInForm(request.POST, restaurant=request.restaurant)
    if form.is_valid():
        data = form.cleaned_data
        try:
            reservation = services.seat_walk_in(
                restaurant=request.restaurant, table=data["table"], party_size=data["guests"],
                customer_name=data["customer_name"], created_by=request.user,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"Walk-in seated at {reservation.table.label} ({reservation.reference}).")
    else:
        messages.error(request, "Choose a Ready table and the number of guests.")
    return redirect("reservations:desk")


# --- Reservation list and detail ---------------------------------------------------------------


def list_context(request):
    filter_form = ReservationFilterForm(request.GET or None)
    reservations = filter_form.filter(
        Reservation.objects.for_restaurant(request.restaurant).select_related("table", "created_by")
    ).order_by("start_at")
    page_obj = Paginator(reservations, 25).get_page(request.GET.get("page"))
    return {"filter_form": filter_form, "reservations": page_obj.object_list, "page_obj": page_obj}


@booking_staff_required
def reservation_list(request):
    return render(request, "reservations/list.html", {"page_title": "Reservations", **list_context(request)})


@booking_staff_required
def reservation_list_fragment(request):
    return render(request, "reservations/_table.html", list_context(request))


@booking_staff_required
def reservation_detail(request, pk):
    reservation = get_object_or_404(
        Reservation.objects.for_restaurant(request.restaurant).select_related("table", "created_by"), pk=pk
    )
    return render(request, "reservations/detail.html", {
        "reservation": reservation,
        "history": reservation.history.select_related("changed_by"),
        "next_statuses": services.allowed_next_statuses(reservation),
        "page_title": f"{reservation.reference} · {reservation.customer_name}",
    })


@booking_staff_required
@require_POST
def change_status(request, pk):
    reservation = get_object_or_404(Reservation.objects.for_restaurant(request.restaurant), pk=pk)
    to_status = request.POST.get("to_status", "")
    if to_status not in Status.values:
        messages.error(request, "Unknown status.")
    else:
        try:
            reservation = services.change_status(reservation, to_status, request.user, note=request.POST.get("note", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{reservation.reference} is now {reservation.get_status_display()}.")
    next_url = request.POST.get("next", "")
    return redirect(next_url if next_url.startswith("/") else reverse("reservations:detail", args=[pk]))
