"""
Table inspections and what they mean for the table.

Three sources share one rule:
  MANUAL  a staff member (usually after scanning the table's QR code)
  TASK    the check a waiter fills in when completing a task
  CAMERA  an external computer-vision service calling POST /api/v1/detections/

A camera can report people and dishes, but it can never declare a table clean:
a table only becomes Ready after a person confirms it.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse

from accounts.models import Role
from notifications import services as notifications
from notifications.models import Notification
from reservations import services as reservation_services
from tables import services as table_services
from tables.models import Cleanliness, TableStatus, TableStatusHistory

from .models import TableInspection, TableInspectionPhoto

Source = TableInspection.Source


def cleanliness_from(*, plates, glasses, leftovers, table_clean, chairs_arranged, ready_for_next):
    """
    Dishes, glasses or food on the table     -> needs clearing
    otherwise not clean / chairs / not confirmed ready -> needs cleaning
    otherwise                                 -> clean
    """
    if plates or glasses or leftovers:
        return Cleanliness.NEEDS_CLEARING
    if not table_clean or not chairs_arranged or not ready_for_next:
        return Cleanliness.NEEDS_CLEANING
    return Cleanliness.CLEAN


def contradictions(answers):
    """Answers that cannot all be true at once (the form shows these as errors)."""
    problems = []
    if answers["ready_for_next"]:
        if answers["customers_present"]:
            problems.append("A table with customers seated cannot be ready for the next customers.")
        if answers["plates_present"] or answers["glasses_present"] or answers["leftovers_present"]:
            problems.append("Clear the plates, glasses and leftovers before marking the table ready.")
        if not answers["table_clean"] or not answers["chairs_arranged"]:
            problems.append("The table must be clean with chairs arranged before it is ready.")
    return problems


def record_inspection(table, user, *, customers_present, plates_present, glasses_present, leftovers_present,
                      table_clean, chairs_arranged, ready_for_next, remarks="", photos=(),
                      source=Source.MANUAL, task=None, before=None):
    """Save an inspection by a person and update the table from it (one transaction)."""
    if source == Source.MANUAL and not table_services.can_inspect(user, table):
        raise PermissionDenied("You are not allowed to inspect this table.")
    answers = {
        "customers_present": customers_present, "plates_present": plates_present,
        "glasses_present": glasses_present, "leftovers_present": leftovers_present,
        "table_clean": table_clean, "chairs_arranged": chairs_arranged, "ready_for_next": ready_for_next,
    }
    problems = contradictions(answers)
    if problems:
        raise ValidationError(problems)

    with transaction.atomic():
        was_occupied = type(table).objects.select_for_update().values_list("is_occupied", flat=True).get(pk=table.pk)
        inspection = TableInspection.objects.create(
            table=table, inspector=user, source=source, task=task, remarks=remarks.strip(), **answers
        )
        for photo in photos:
            item = TableInspectionPhoto(inspection=inspection)
            item.image.save("photo.jpg", photo, save=True)

        cleanliness = cleanliness_from(
            plates=plates_present, glasses=glasses_present, leftovers=leftovers_present,
            table_clean=table_clean, chairs_arranged=chairs_arranged, ready_for_next=ready_for_next,
        )
        history_source = TableStatusHistory.Source.TASK if source == Source.TASK else TableStatusHistory.Source.INSPECTION
        table = table_services.change_table_state(
            table, source=history_source, user=user, inspected=True, before=before,
            is_occupied=customers_present, cleanliness=cleanliness, note=f"{inspection.reference}",
        )
        if was_occupied and not customers_present:
            reservation_services.finish_seated_reservations(table, user, note=f"Guests left ({inspection.reference})")
        inspection.resulting_status = table_services.status_of(table)
        inspection.save(update_fields=["resulting_status"])

        if source == Source.MANUAL:
            notify_managers(inspection, exclude=user)
    return inspection


def record_detection(table, *, customers_present, plates_present, glasses_present, leftovers_present,
                     confidence=None, device_id=""):
    """
    A camera / computer-vision result. Stored always; applied to the table only
    when its confidence is high enough. It can set occupancy and report dishes,
    and it can move "dishes on the table" to "needs cleaning" once the dishes
    are gone - but never to clean.
    """
    applied = confidence is None or confidence >= settings.DETECTION_MIN_CONFIDENCE
    with transaction.atomic():
        table = type(table).objects.select_for_update().select_related("restaurant").get(pk=table.pk)
        was_occupied = table.is_occupied
        inspection = TableInspection.objects.create(
            table=table, source=Source.CAMERA, device_id=device_id, confidence=confidence, applied=applied,
            customers_present=customers_present, plates_present=plates_present, glasses_present=glasses_present,
            leftovers_present=leftovers_present, table_clean=None, chairs_arranged=None, ready_for_next=False,
        )
        if applied:
            if plates_present or glasses_present or leftovers_present:
                cleanliness = Cleanliness.NEEDS_CLEARING
            elif table.cleanliness == Cleanliness.NEEDS_CLEARING:
                cleanliness = Cleanliness.NEEDS_CLEANING  # dishes gone; a person still has to clean and confirm
            else:
                cleanliness = None  # unchanged
            note = f"{inspection.reference} from {device_id or 'camera'}"
            if confidence is not None:
                note += f", confidence {confidence:.2f}"
            table = table_services.change_table_state(
                table, source=TableStatusHistory.Source.CAMERA, inspected=True,
                is_occupied=customers_present, cleanliness=cleanliness, note=note,
            )
            if was_occupied and not customers_present:
                reservation_services.finish_seated_reservations(table, None, note=f"Guests left ({inspection.reference})")
        inspection.resulting_status = table_services.status_of(table)
        inspection.save(update_fields=["resulting_status"])
        if applied:
            notify_managers(inspection)
    return inspection


def notify_managers(inspection, exclude=None):
    table = inspection.table
    who = "camera" if inspection.source == Source.CAMERA else (
        inspection.inspector.get_full_name() or inspection.inspector.username
    )
    managers = [m for m in notifications.staff_with_roles(table.restaurant, Role.MANAGER) if m != exclude]
    notifications.notify(
        managers, restaurant=table.restaurant, kind=Notification.Kind.INSPECTION,
        title=f"{table.label} inspected: {TableStatus(inspection.resulting_status).label}",
        message=f"{inspection.reference} by {who}.",
        link=reverse("inspections:detail", args=[inspection.pk]),
    )
