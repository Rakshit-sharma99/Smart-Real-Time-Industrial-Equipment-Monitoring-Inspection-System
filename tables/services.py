"""
Table state - the ONE place where a table's condition changes.

Inspections, camera detections, waiter tasks, reservations and manager actions
all call these functions, so the audit log, the notifications and the
real-time updates are never forgotten.
"""
import io
from collections import Counter
from datetime import timedelta

import qrcode
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone
from qrcode.constants import ERROR_CORRECT_M
from qrcode.image.pil import PilImage

from accounts.models import Role
from core import realtime
from core.permissions import get_role
from notifications import services as notifications
from notifications.models import Notification
from reservations.models import Reservation
from tasks.models import WaiterTask

from .models import NEEDS_CLEANING_STATUSES, RestaurantTable, TableStatus, TableStatusHistory, compute_status

# A status change within this window before a booking is flagged to the receptionists.
BOOKING_ALERT_WINDOW = timedelta(hours=3)
Source = TableStatusHistory.Source


# --- Reading the live state ------------------------------------------------------------


def live_tables(restaurant, now=None):
    """
    Tables of one restaurant, annotated with everything the status rule needs
    (task in progress, booking hold, next booking) in a single query.
    """
    now = now or timezone.now()
    hold = timedelta(minutes=restaurant.reservation_hold_minutes)
    in_progress = WaiterTask.objects.filter(table=OuterRef("pk"), status=WaiterTask.Status.IN_PROGRESS)
    holding = Reservation.objects.filter(
        table=OuterRef("pk"), status__in=Reservation.WAITING_STATUSES, start_at__lte=now + hold, end_at__gt=now
    )
    upcoming = Reservation.objects.filter(
        table=OuterRef("pk"), status__in=Reservation.WAITING_STATUSES, end_at__gt=now
    ).order_by("start_at")
    return (
        RestaurantTable.objects.filter(restaurant=restaurant)
        .select_related("section", "assigned_waiter")
        .annotate(
            cleaning_in_progress=Exists(in_progress),
            reservation_hold=Exists(holding),
            next_booking_at=Subquery(upcoming.values("start_at")[:1]),
            next_booking_party=Subquery(upcoming.values("party_size")[:1]),
        )
    )


def status_of(table, now=None):
    """The displayed status of one table (works for annotated and plain table objects)."""
    if hasattr(table, "cleaning_in_progress"):
        in_progress, hold = table.cleaning_in_progress, table.reservation_hold
    else:
        now = now or timezone.now()
        hold_minutes = timedelta(minutes=table.restaurant.reservation_hold_minutes)
        in_progress = table.tasks.filter(status=WaiterTask.Status.IN_PROGRESS).exists()
        hold = table.reservations.filter(
            status__in=Reservation.WAITING_STATUSES, start_at__lte=now + hold_minutes, end_at__gt=now
        ).exists()
    return compute_status(
        is_active=table.is_active,
        is_blocked=table.is_blocked,
        is_occupied=table.is_occupied,
        cleanliness=table.cleanliness,
        cleaning_in_progress=in_progress,
        reservation_hold=hold,
    )


def with_status(tables):
    """Evaluate tables (ideally from live_tables) and attach .status and .status_label."""
    result = list(tables)
    for table in result:
        table.status = status_of(table)
        table.status_label = TableStatus(table.status).label
    return result


def floor_summary(tables):
    """Counts for the dashboards, from tables that already carry .status."""
    active = [t for t in tables if t.is_active]
    counts = Counter(t.status for t in active)
    occupied, blocked = counts[TableStatus.OCCUPIED], counts[TableStatus.BLOCKED]
    return {
        "total": len(active),
        "ready": counts[TableStatus.READY],
        "occupied": occupied,
        "reserved": counts[TableStatus.RESERVED],
        "needs_clearing": counts[TableStatus.VACANT_NEEDS_CLEARING],
        "cleaning_required": counts[TableStatus.CLEANING_REQUIRED],
        "needs_cleaning": counts[TableStatus.VACANT_NEEDS_CLEARING] + counts[TableStatus.CLEANING_REQUIRED],
        "cleaning": counts[TableStatus.CLEANING_IN_PROGRESS],
        "blocked": blocked,
        "vacant": len(active) - occupied - blocked,
        "seats_ready": sum(t.capacity for t in active if t.status == TableStatus.READY),
    }


# --- Changing the state --------------------------------------------------------------------


def change_table_state(table, *, source, user=None, note="", is_occupied=None, cleanliness=None,
                       is_blocked=None, block_reason=None, inspected=False, before=None):
    """
    Change the stored facts of a table (only the arguments that are given).
    Locks the row, saves, writes the audit row and announces the change after commit.
    `before` lets a caller that already changed a task/booking in the same
    transaction pass the status the table had before that change.
    """
    with transaction.atomic():
        table = RestaurantTable.objects.select_for_update().select_related("restaurant").get(pk=table.pk)
        if before is None:
            before = status_of(table)
        changed = False
        for field, value in (("is_occupied", is_occupied), ("cleanliness", cleanliness),
                             ("is_blocked", is_blocked), ("block_reason", block_reason)):
            if value is not None and getattr(table, field) != value:
                setattr(table, field, value)
                changed = True
        now = timezone.now()
        if changed:
            table.status_updated_at = now
        if inspected:
            table.last_inspected_at = now
        table.save()
        log_status_change(table, before, source=source, user=user, note=note, force=changed)
    return table


def log_status_change(table, before, *, source, user=None, note="", force=False):
    """
    After any change that may alter a table's displayed status (also task and
    reservation changes): write the audit row, notify and broadcast - but only
    if something actually changed. Call it inside the same transaction.
    """
    after = status_of(table)
    if after == before and not force:
        return after
    table.status_updated_at = timezone.now()
    RestaurantTable.objects.filter(pk=table.pk).update(status_updated_at=table.status_updated_at)
    TableStatusHistory.objects.create(
        table=table, from_status=before, to_status=after, is_occupied=table.is_occupied,
        cleanliness=table.cleanliness, is_blocked=table.is_blocked, source=source, changed_by=user, note=note,
    )
    realtime.publish(
        "table.changed", restaurant_id=table.restaurant_id, topics=["tables"], everyone=True,
        data={"table": table.label, "status": after},
    )
    notify_status_change(table, before, after)
    return after


def notify_status_change(table, before, after):
    restaurant = table.restaurant
    link = reverse("tables:detail", args=[table.pk])
    label = TableStatus(after).label
    if after in NEEDS_CLEANING_STATUSES and before not in NEEDS_CLEANING_STATUSES:
        notifications.notify_roles(
            restaurant, [Role.MANAGER], kind=Notification.Kind.CLEANING_NEEDED,
            title=f"{table.label} needs cleaning", message=f"{table.label} is now: {label}.", link=link,
        )
    if after == TableStatus.READY and before in (*NEEDS_CLEANING_STATUSES, TableStatus.CLEANING_IN_PROGRESS):
        notifications.notify_roles(
            restaurant, [Role.RECEPTIONIST], kind=Notification.Kind.TABLE_READY,
            title=f"{table.label} is ready", message=f"{table.label} ({table.capacity} seats) can take guests.",
            link=link,
        )
    usable = (TableStatus.READY, TableStatus.RESERVED)
    if before in usable and after not in usable:
        soon = table.reservations.filter(
            status__in=Reservation.WAITING_STATUSES,
            start_at__gte=timezone.now(),
            start_at__lte=timezone.now() + BOOKING_ALERT_WINDOW,
        ).order_by("start_at").first()
        if soon:
            notifications.notify_roles(
                restaurant, [Role.RECEPTIONIST, Role.MANAGER], kind=Notification.Kind.BOOKING_AFFECTED,
                title=f"{table.label} is now {label}",
                message=f"Booking {soon.reference} at {timezone.localtime(soon.start_at):%H:%M} uses this table.",
                link=reverse("reservations:detail", args=[soon.pk]),
            )


def set_blocked(table, *, blocked, reason, user):
    """Block (temporarily unavailable, reason required) or unblock a table."""
    reason = (reason or "").strip()
    if blocked and not reason:
        raise ValidationError({"reason": "Give a reason for blocking the table."})
    return change_table_state(
        table, source=Source.BLOCK, user=user, is_blocked=blocked, block_reason=reason if blocked else "",
        note=reason or "Unblocked",
    )


def override_state(table, *, is_occupied, cleanliness, reason, user):
    """Manager correction of the stored facts. The reason is kept in the audit log."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required for a manual override."})
    return change_table_state(
        table, source=Source.OVERRIDE, user=user, is_occupied=is_occupied, cleanliness=cleanliness,
        note=f"Override: {reason}",
    )


def assign_waiter(table, waiter, *, user):
    """Make one waiter responsible for a table (or None to clear)."""
    if waiter is not None:
        if get_role(waiter) != Role.WAITER or waiter.profile.restaurant_id != table.restaurant_id:
            raise ValidationError({"waiter": "Choose a waiter of this restaurant."})
    with transaction.atomic():
        table = RestaurantTable.objects.select_for_update().select_related("restaurant").get(pk=table.pk)
        table.assigned_waiter = waiter
        table.save(update_fields=["assigned_waiter", "updated_at"])
        name = (waiter.get_full_name() or waiter.username) if waiter else "nobody"
        log_status_change(table, status_of(table), source=Source.ASSIGNMENT, user=user,
                          note=f"Assigned waiter: {name}", force=True)
    return table


def set_active(table, *, active, user):
    """Deactivate (history is kept) or reactivate a table."""
    if not active and table.reservations.active().filter(end_at__gt=timezone.now()).exists():
        raise ValidationError("Cancel or move the upcoming reservations of this table first.")
    with transaction.atomic():
        table = RestaurantTable.objects.select_for_update().select_related("restaurant").get(pk=table.pk)
        before = status_of(table)
        table.is_active = active
        table.save(update_fields=["is_active", "updated_at"])
        log_status_change(table, before, source=Source.SETUP, user=user,
                          note="Reactivated" if active else "Deactivated")
    return table


# --- Permissions on a single table ------------------------------------------------------------


def can_inspect(user, table):
    """
    Managers may inspect any table. Waiters may inspect a table that is
    assigned to them, has no assigned waiter, or has an open task of theirs.
    Receptionists never record inspections.
    """
    role = get_role(user)
    if role == Role.MANAGER:
        return table.is_active
    if role != Role.WAITER or not table.is_active:
        return False
    if table.assigned_waiter_id in (None, user.pk):
        return True
    return table.tasks.open().filter(assigned_to=user).exists()


# --- QR codes -------------------------------------------------------------------------------------


def make_qr_png(text, box_size=10):
    """PNG bytes of a QR code for `text`. Generated in memory, never stored."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
