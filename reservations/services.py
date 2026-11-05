"""
Reservations: time-based availability, conflict-free booking and the status workflow.

A booking occupies a time slot [start_at, end_at) on one table. Two active
bookings of the same table may never overlap. A future booking does NOT make a
table unavailable now: the table only shows as Reserved during the hold window
(restaurant.reservation_hold_minutes before the start, until the end).

    PENDING -> CONFIRMED -> ARRIVED -> SEATED -> COMPLETED
    PENDING / CONFIRMED / ARRIVED -> CANCELLED
    PENDING / CONFIRMED -> NO_SHOW   (only after the booking time)
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core import realtime
from core.permissions import get_role
from notifications import services as notifications
from notifications.models import Notification
from tables import services as table_services
from tables.models import Cleanliness, RestaurantTable, TableStatus, TableStatusHistory

from .models import Reservation, ReservationHistory

Status = Reservation.Status
BOOKING_ROLES = (Role.MANAGER, Role.RECEPTIONIST)
# A booking may start this little in the past (clocks, typing) and still be accepted.
GRACE = timedelta(minutes=5)

TRANSITIONS = {
    (Status.PENDING, Status.CONFIRMED),
    (Status.PENDING, Status.ARRIVED),
    (Status.CONFIRMED, Status.ARRIVED),
    (Status.PENDING, Status.SEATED),
    (Status.CONFIRMED, Status.SEATED),
    (Status.ARRIVED, Status.SEATED),
    (Status.SEATED, Status.COMPLETED),
    (Status.PENDING, Status.CANCELLED),
    (Status.CONFIRMED, Status.CANCELLED),
    (Status.ARRIVED, Status.CANCELLED),
    (Status.PENDING, Status.NO_SHOW),
    (Status.CONFIRMED, Status.NO_SHOW),
}


class BookingConflict(ValidationError):
    """The table already has an overlapping active booking."""


def allowed_next_statuses(reservation):
    return [to for (frm, to) in sorted(TRANSITIONS) if frm == reservation.status]


def end_for(restaurant, start, duration_minutes=None):
    return start + timedelta(minutes=duration_minutes or restaurant.reservation_minutes)


def conflicts(table, start, end, exclude_pk=None):
    """Active bookings of `table` whose time slot overlaps [start, end)."""
    clashes = Reservation.objects.filter(table=table).active().overlapping(start, end)
    if exclude_pk:
        clashes = clashes.exclude(pk=exclude_pk)
    return clashes


def available_tables(restaurant, *, start, end, party_size, section=None, walk_in=False):
    """
    Tables that can take `party_size` guests for [start, end): active, not
    blocked, big enough and free of overlapping bookings. For a walk-in (now)
    the table must also be Ready right now. Smallest suitable tables first.
    """
    tables = table_services.live_tables(restaurant).filter(is_active=True, is_blocked=False, capacity__gte=party_size)
    if section is not None:
        tables = tables.filter(section=section)
    busy = set(
        Reservation.objects.filter(table__restaurant=restaurant).active().overlapping(start, end)
        .values_list("table_id", flat=True)
    )
    result = [t for t in table_services.with_status(tables) if t.pk not in busy]
    if walk_in:
        result = [t for t in result if t.status == TableStatus.READY]
    return sorted(result, key=lambda t: (t.capacity, t.number))


def check_booking_staff(user):
    if get_role(user) not in BOOKING_ROLES:
        raise PermissionDenied("Only managers and receptionists manage reservations.")


def create_reservation(*, restaurant, table, customer_name, party_size, start_at, created_by, phone="",
                       duration_minutes=None, remarks="", source=Reservation.Source.BOOKING,
                       status=Status.CONFIRMED):
    """Book a table. The conflict check and the insert happen in one locked transaction."""
    check_booking_staff(created_by)
    customer_name = (customer_name or "").strip()
    errors = {}
    if table.restaurant_id != restaurant.pk:
        errors["table"] = "This table belongs to another restaurant."
    elif not table.is_active or table.is_blocked:
        errors["table"] = f"{table.label} is not available for bookings right now."
    elif party_size > table.capacity:
        errors["party_size"] = f"{table.label} seats {table.capacity}; this party has {party_size}."
    if not customer_name:
        errors["customer_name"] = "Enter the customer's name."
    if source == Reservation.Source.BOOKING and not phone:
        errors["phone"] = "A phone number is required for bookings."
    if source == Reservation.Source.BOOKING and start_at < timezone.now() - GRACE:
        errors["start_at"] = "The booking time is in the past."
    if errors:
        raise ValidationError(errors)

    end_at = end_for(restaurant, start_at, duration_minutes)
    with transaction.atomic():
        # Lock the table row so two receptionists cannot book the same slot at the same moment.
        table = RestaurantTable.objects.select_for_update().select_related("restaurant").get(pk=table.pk)
        clash = conflicts(table, start_at, end_at).first()
        if clash:
            raise BookingConflict({"table": (
                f"{table.label} is already booked {timezone.localtime(clash.start_at):%d %b %H:%M}"
                f"–{timezone.localtime(clash.end_at):%H:%M} ({clash.reference})."
            )})
        before = table_services.status_of(table)
        reservation = Reservation.objects.create(
            restaurant=restaurant, table=table, customer_name=customer_name, phone=phone.strip(),
            party_size=party_size, start_at=start_at, end_at=end_at, status=status, source=source,
            remarks=remarks.strip(), created_by=created_by,
            confirmed_at=timezone.now() if status == Status.CONFIRMED else None,
            arrived_at=timezone.now() if status == Status.ARRIVED else None,
        )
        ReservationHistory.objects.create(reservation=reservation, to_status=status, changed_by=created_by,
                                          note="Walk-in" if source == Reservation.Source.WALK_IN else "Booked")
        table_services.log_status_change(table, before, source=TableStatusHistory.Source.RESERVATION,
                                         user=created_by, note=f"{reservation.reference} booked")
        publish(reservation)
        if source == Reservation.Source.BOOKING:
            when = f"{timezone.localtime(start_at):%d %b %H:%M}"
            link = reverse("reservations:detail", args=[reservation.pk])
            notifications.notify_roles(
                restaurant, [Role.MANAGER], kind=Notification.Kind.RESERVATION_CREATED,
                title=f"New booking {reservation.reference}: {party_size} guests, {when}",
                message=f"{table.label} for {customer_name}.", link=link,
            )
            notifications.notify(
                [created_by], restaurant=restaurant, kind=Notification.Kind.RESERVATION_CONFIRMED,
                title=f"Booking {reservation.reference} confirmed", message=f"{table.label}, {when}.", link=link,
            )
    return reservation


def seat_walk_in(*, restaurant, table, party_size, created_by, customer_name="", phone=""):
    """Walk-in guests: a booking that starts now, immediately seated. The table must be Ready."""
    with transaction.atomic():
        if table_services.status_of(table) != TableStatus.READY:
            raise ValidationError({"table": f"{table.label} is not ready for guests right now."})
        reservation = create_reservation(
            restaurant=restaurant, table=table, customer_name=customer_name or "Walk-in", phone=phone,
            party_size=party_size, start_at=timezone.now(), created_by=created_by,
            source=Reservation.Source.WALK_IN, status=Status.ARRIVED,
        )
        return change_status(reservation, Status.SEATED, created_by, note="Walk-in seated")


def change_status(reservation, to_status, user, note=""):
    """Move a booking along the workflow. Seating and completing also update the table."""
    check_booking_staff(user)
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().select_related("table", "restaurant").get(pk=reservation.pk)
        from_status = reservation.status
        if (from_status, to_status) not in TRANSITIONS:
            raise ValidationError(f"A booking cannot move from {Status(from_status).label} to {Status(to_status).label}.")
        table = RestaurantTable.objects.select_for_update().select_related("restaurant").get(pk=reservation.table_id)
        before = table_services.status_of(table)
        now = timezone.now()

        if to_status == Status.SEATED:
            if not table.is_active or table.is_blocked or table.is_occupied or table.cleanliness != Cleanliness.CLEAN:
                raise ValidationError(f"{table.label} is not ready for guests ({TableStatus(before).label}).")
            reservation.seated_at = now
            reservation.arrived_at = reservation.arrived_at or now
        elif to_status == Status.ARRIVED:
            reservation.arrived_at = now
        elif to_status == Status.CONFIRMED:
            reservation.confirmed_at = now
        elif to_status == Status.COMPLETED:
            reservation.completed_at = now
        elif to_status == Status.CANCELLED:
            reservation.cancelled_at = now
        elif to_status == Status.NO_SHOW and now < reservation.start_at:
            raise ValidationError("A booking can only be marked as no-show after its start time.")

        reservation.status = to_status
        reservation.save()
        ReservationHistory.objects.create(reservation=reservation, from_status=from_status, to_status=to_status,
                                          changed_by=user, note=note)
        source = TableStatusHistory.Source.RESERVATION
        if to_status == Status.SEATED:
            table_services.change_table_state(table, source=source, user=user, is_occupied=True, before=before,
                                              note=f"{reservation.reference} seated")
        elif to_status == Status.COMPLETED:
            table_services.change_table_state(table, source=source, user=user, is_occupied=False,
                                              cleanliness=Cleanliness.NEEDS_CLEARING, before=before,
                                              note=f"{reservation.reference} guests left")
        else:
            table_services.log_status_change(table, before, source=source, user=user,
                                             note=f"{reservation.reference} {Status(to_status).label.lower()}")
        publish(reservation)
        if to_status == Status.CANCELLED:
            notifications.notify_roles(
                reservation.restaurant, [Role.MANAGER], kind=Notification.Kind.RESERVATION_CANCELLED,
                title=f"Booking {reservation.reference} cancelled",
                message=f"{table.label}, {timezone.localtime(reservation.start_at):%d %b %H:%M}.",
                link=reverse("reservations:detail", args=[reservation.pk]),
            )
    return reservation


def finish_seated_reservations(table, user, note):
    """Guests left (seen by an inspection or a camera): close their booking. The table is already updated."""
    for reservation in Reservation.objects.select_for_update().filter(table=table, status=Status.SEATED):
        reservation.status = Status.COMPLETED
        reservation.completed_at = timezone.now()
        reservation.save(update_fields=["status", "completed_at"])
        ReservationHistory.objects.create(reservation=reservation, from_status=Status.SEATED,
                                          to_status=Status.COMPLETED, changed_by=user, note=note)
        publish(reservation)


def publish(reservation):
    """Bookings matter to managers and receptionists (waiters do not see customer details)."""
    realtime.publish(
        "reservation.changed", restaurant_id=reservation.restaurant_id, topics=["reservations"],
        roles=BOOKING_ROLES, data={"reservation": reservation.reference, "status": reservation.status},
    )
