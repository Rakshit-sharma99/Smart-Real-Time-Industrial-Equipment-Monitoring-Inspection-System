"""
Maintenance task rules. All allowed status changes are in ONE table below.

    OPEN -> ASSIGNED          admin              an assignee is required
    ASSIGNED -> OPEN          admin              clears the assignee
    ASSIGNED -> IN_PROGRESS   assignee or admin
    IN_PROGRESS -> COMPLETED  assignee or admin  resolution notes required; sets completed_at

COMPLETED is terminal. Every change writes a TaskStatusChange history row.
Completing a task closes its linked defect and recomputes equipment health from
readings and open defects - it never simply marks a machine healthy.
"""
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.permissions import is_admin
from equipment.services import recompute_health
from inspections.models import Defect

from .models import MaintenanceTask, TaskStatusChange

Status = MaintenanceTask.Status
ADMIN = "admin"
ASSIGNEE_OR_ADMIN = "assignee or admin"


@dataclass(frozen=True)
class Transition:
    who: str
    needs_assignee: bool = False
    clears_assignee: bool = False
    needs_resolution_notes: bool = False


TRANSITIONS = {
    (Status.OPEN, Status.ASSIGNED): Transition(who=ADMIN, needs_assignee=True),
    (Status.ASSIGNED, Status.OPEN): Transition(who=ADMIN, clears_assignee=True),
    (Status.ASSIGNED, Status.IN_PROGRESS): Transition(who=ASSIGNEE_OR_ADMIN),
    (Status.IN_PROGRESS, Status.COMPLETED): Transition(who=ASSIGNEE_OR_ADMIN, needs_resolution_notes=True),
}

# Defect severity -> suggested task priority when converting.
PRIORITY_FOR_DEFECT = {
    Defect.Severity.HIGH: MaintenanceTask.Priority.HIGH,
    Defect.Severity.MEDIUM: MaintenanceTask.Priority.MEDIUM,
    Defect.Severity.LOW: MaintenanceTask.Priority.LOW,
}


class TransitionError(Exception):
    """A status change that the transition table does not allow."""


def may_use(rule, task, user):
    if is_admin(user):
        return True
    return rule.who == ASSIGNEE_OR_ADMIN and task.assigned_to_id == user.pk


def allowed_transitions(task, user):
    """Target statuses this user may move the task to right now (used to show buttons)."""
    return [to for (frm, to), rule in TRANSITIONS.items() if frm == task.status and may_use(rule, task, user)]


def change_status(task, to_status, user, *, assignee=None, resolution_notes="", note=""):
    with transaction.atomic():
        task = MaintenanceTask.objects.select_for_update().select_related("equipment").get(pk=task.pk)
        from_status = task.status
        rule = TRANSITIONS.get((from_status, to_status))
        if rule is None:
            raise TransitionError(
                f"A task cannot move from {Status(from_status).label} to {Status(to_status).label}."
            )
        if not may_use(rule, task, user):
            raise PermissionDenied("You are not allowed to make this change.")

        if rule.needs_assignee:
            if assignee is None:
                raise ValidationError({"assignee": "Choose who will do this task."})
            task.assigned_to = assignee
        if rule.clears_assignee:
            task.assigned_to = None
        if rule.needs_resolution_notes:
            resolution_notes = (resolution_notes or "").strip()
            if not resolution_notes:
                raise ValidationError({"resolution_notes": "Resolution notes are required to complete a task."})
            task.resolution_notes = resolution_notes
            task.completed_at = timezone.now()

        task.status = to_status
        task.save()
        TaskStatusChange.objects.create(
            task=task, from_status=from_status, to_status=to_status, changed_by=user,
            note=note or resolution_notes or (f"Assigned to {assignee.get_full_name() or assignee.username}" if assignee else ""),
        )

        if to_status == Status.COMPLETED:
            if task.defect_id:
                Defect.objects.filter(pk=task.defect_id).update(status=Defect.Status.CLOSED)
            recompute_health(task.equipment)  # from readings + remaining open defects
    return task


def create_task(*, equipment, title, description, priority, due_date, created_by,
                assignee=None, alert=None, defect=None):
    """
    Admin creates a task (optionally from a defect or an alert).
    A linked defect moves to IN_MAINTENANCE. Alerts are never changed by tasks.
    """
    if not is_admin(created_by):
        raise PermissionDenied("Only admins create maintenance tasks.")
    with transaction.atomic():
        if defect is not None:
            defect = Defect.objects.select_for_update().get(pk=defect.pk)
            if defect.status != Defect.Status.OPEN:
                raise TransitionError("This defect is already in maintenance or closed.")
            equipment = defect.equipment
        if alert is not None:
            equipment = alert.equipment
        task = MaintenanceTask.objects.create(
            equipment=equipment, title=title, description=description, priority=priority,
            due_date=due_date, created_by=created_by, alert=alert, defect=defect, status=Status.OPEN,
        )
        TaskStatusChange.objects.create(task=task, to_status=Status.OPEN, changed_by=created_by, note="Task created")
        if defect is not None:
            defect.status = Defect.Status.IN_MAINTENANCE
            defect.save(update_fields=["status"])
        if assignee is not None:
            task = change_status(task, Status.ASSIGNED, created_by, assignee=assignee)
    return task


def prefill_from_defect(defect):
    return {
        "equipment": defect.equipment,
        "title": f"Fix defect {defect.reference} on {defect.equipment.equipment_code}",
        "description": defect.description,
        "priority": PRIORITY_FOR_DEFECT[defect.severity],
    }


def prefill_from_alert(alert):
    priority = MaintenanceTask.Priority.CRITICAL if alert.severity == "CRITICAL" else MaintenanceTask.Priority.MEDIUM
    return {
        "equipment": alert.equipment,
        "title": f"Investigate {alert.metric_info.label.lower()} alert {alert.reference} on {alert.equipment.equipment_code}",
        "description": alert.description,
        "priority": priority,
    }
