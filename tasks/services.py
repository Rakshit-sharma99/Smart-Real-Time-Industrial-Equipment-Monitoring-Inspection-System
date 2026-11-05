"""
Waiter tasks. All allowed status changes are in ONE table:

    OPEN -> ASSIGNED          manager            a waiter is required
    ASSIGNED -> OPEN          manager            clears the waiter
    ASSIGNED -> IN_PROGRESS   assignee or manager
    IN_PROGRESS -> COMPLETED  assignee or manager  needs the table check

Other actions: accept (assignee), progress note (assignee/manager),
reassign and change priority (manager). Every action writes a history row.

Completing a task records the waiter's table check as an inspection, and that
inspection updates the table - so a table becomes Ready only when the check
says it really is clear, clean, arranged and ready.
"""
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from core import realtime
from core.permissions import get_role, is_manager
from inspections import services as inspection_services
from inspections.models import TableInspection
from notifications import services as notifications
from notifications.models import Notification
from tables import services as table_services
from tables.models import TableStatus, TableStatusHistory

from .models import WaiterTask, WaiterTaskHistory

Status = WaiterTask.Status
Action = WaiterTaskHistory.Action
MANAGER = "manager"
ASSIGNEE_OR_MANAGER = "assignee or manager"


@dataclass(frozen=True)
class Transition:
    who: str
    needs_assignee: bool = False
    clears_assignee: bool = False
    needs_table_check: bool = False


TRANSITIONS = {
    (Status.OPEN, Status.ASSIGNED): Transition(who=MANAGER, needs_assignee=True),
    (Status.ASSIGNED, Status.OPEN): Transition(who=MANAGER, clears_assignee=True),
    (Status.ASSIGNED, Status.IN_PROGRESS): Transition(who=ASSIGNEE_OR_MANAGER),
    (Status.IN_PROGRESS, Status.COMPLETED): Transition(who=ASSIGNEE_OR_MANAGER, needs_table_check=True),
}


def may_use(rule, task, user):
    if is_manager(user):
        return True
    return rule.who == ASSIGNEE_OR_MANAGER and task.assigned_to_id == user.pk


def allowed_transitions(task, user):
    """Target statuses this user may move the task to right now (used to show buttons)."""
    return [to for (frm, to), rule in TRANSITIONS.items() if frm == task.status and may_use(rule, task, user)]


def check_waiter(task, waiter):
    if waiter is None or get_role(waiter) != Role.WAITER or not waiter.is_active \
            or waiter.profile.restaurant_id != task.restaurant_id:
        raise ValidationError({"assignee": "Choose an active waiter of this restaurant."})


def locked(task):
    return WaiterTask.objects.select_for_update().select_related("table", "restaurant").get(pk=task.pk)


def history(task, action, user, from_status, note=""):
    WaiterTaskHistory.objects.create(task=task, action=action, from_status=from_status or "",
                                     to_status=task.status, changed_by=user, note=note)


def publish(task, extra_user_ids=()):
    """Task changes go to the managers and to the waiter(s) concerned."""
    realtime.publish(
        "task.changed", restaurant_id=task.restaurant_id, topics=["tasks"], roles=[Role.MANAGER],
        user_ids=[task.assigned_to_id, *extra_user_ids], data={"task": task.task_code, "status": task.status},
    )


def link_for(task):
    return reverse("tasks:detail", args=[task.pk])


def name(user):
    return (user.get_full_name() or user.username) if user else "nobody"


# --- Creating and assigning -----------------------------------------------------------------


def create_task(*, table, task_type, priority, created_by, description="", due_at=None, assignee=None):
    if not is_manager(created_by):
        raise PermissionDenied("Only managers create waiter tasks.")
    if not table.is_active:
        raise ValidationError({"table": f"{table.label} is deactivated."})
    restaurant = table.restaurant
    with transaction.atomic():
        task = WaiterTask.objects.create(
            restaurant=restaurant, table=table, task_type=task_type, priority=priority,
            description=description.strip(), created_by=created_by, status=Status.OPEN,
            due_at=due_at or timezone.now() + timedelta(minutes=restaurant.cleaning_deadline_minutes),
        )
        history(task, Action.CREATED, created_by, "", note=task.get_task_type_display())
        publish(task)
        if assignee is not None:
            task = assign(task, assignee, user=created_by)
    return task


def assign(task, waiter, *, user):
    """OPEN -> ASSIGNED (manager)."""
    with transaction.atomic():
        task = locked(task)
        rule = TRANSITIONS.get((task.status, Status.ASSIGNED))
        if rule is None:
            raise ValidationError("Only open tasks can be assigned; use Reassign for assigned tasks.")
        if not may_use(rule, task, user):
            raise PermissionDenied("Only managers assign tasks.")
        check_waiter(task, waiter)
        from_status = task.status
        task.assigned_to, task.status, task.assigned_at = waiter, Status.ASSIGNED, timezone.now()
        task.save()
        history(task, Action.ASSIGNED, user, from_status, note=f"Assigned to {name(waiter)}")
        publish(task)
        notifications.notify(
            [waiter], restaurant=task.restaurant, kind=Notification.Kind.TASK_ASSIGNED,
            title=f"New task {task.task_code}: {task.get_task_type_display()} at {task.table.label}",
            message=f"Priority {task.get_priority_display()}" + (
                f", due {timezone.localtime(task.due_at):%H:%M}" if task.due_at else ""),
            link=link_for(task),
        )
    return task


def unassign(task, *, user):
    """ASSIGNED -> OPEN (manager)."""
    with transaction.atomic():
        task = locked(task)
        rule = TRANSITIONS.get((task.status, Status.OPEN))
        if rule is None:
            raise ValidationError("Only assigned tasks can be unassigned.")
        if not may_use(rule, task, user):
            raise PermissionDenied("Only managers unassign tasks.")
        previous = task.assigned_to_id
        task.assigned_to, task.status, task.assigned_at, task.accepted_at = None, Status.OPEN, None, None
        task.save()
        history(task, Action.UNASSIGNED, user, Status.ASSIGNED)
        publish(task, extra_user_ids=[previous])
    return task


def reassign(task, waiter, *, user):
    """Give an assigned or in-progress task to another waiter (manager). It restarts as Assigned."""
    if not is_manager(user):
        raise PermissionDenied("Only managers reassign tasks.")
    with transaction.atomic():
        task = locked(task)
        if task.status not in (Status.ASSIGNED, Status.IN_PROGRESS):
            raise ValidationError("Only assigned or in-progress tasks can be reassigned.")
        check_waiter(task, waiter)
        if waiter.pk == task.assigned_to_id:
            raise ValidationError({"assignee": "The task is already assigned to this waiter."})
        before = table_services.status_of(task.table)
        previous = task.assigned_to
        from_status = task.status
        task.assigned_to, task.status = waiter, Status.ASSIGNED
        task.assigned_at, task.accepted_at, task.started_at = timezone.now(), None, None
        task.save()
        history(task, Action.REASSIGNED, user, from_status, note=f"{name(previous)} -> {name(waiter)}")
        table_services.log_status_change(task.table, before, source=TableStatusHistory.Source.TASK, user=user,
                                         note=f"{task.task_code} reassigned")
        publish(task, extra_user_ids=[previous.pk if previous else None])
        notifications.notify(
            [waiter], restaurant=task.restaurant, kind=Notification.Kind.TASK_REASSIGNED,
            title=f"Task {task.task_code} reassigned to you: {task.get_task_type_display()} at {task.table.label}",
            link=link_for(task),
        )
        notifications.notify(
            [previous], restaurant=task.restaurant, kind=Notification.Kind.TASK_REASSIGNED,
            title=f"Task {task.task_code} moved to {name(waiter)}", link=link_for(task),
        )
    return task


def change_priority(task, priority, *, user):
    if not is_manager(user):
        raise PermissionDenied("Only managers change task priority.")
    with transaction.atomic():
        task = locked(task)
        if task.status == Status.COMPLETED:
            raise ValidationError("Completed tasks cannot change priority.")
        if priority == task.priority:
            return task
        old = task.get_priority_display()
        task.priority = priority
        task.save(update_fields=["priority"])
        history(task, Action.PRIORITY, user, task.status, note=f"{old} -> {task.get_priority_display()}")
        publish(task)
        notifications.notify(
            [task.assigned_to], restaurant=task.restaurant, kind=Notification.Kind.TASK_PRIORITY,
            title=f"Task {task.task_code} is now {task.get_priority_display()} priority",
            message=f"{task.get_task_type_display()} at {task.table.label}.", link=link_for(task),
        )
    return task


# --- The waiter's actions ------------------------------------------------------------------------


def accept(task, *, user):
    """The assigned waiter confirms they have seen the task (status stays Assigned)."""
    with transaction.atomic():
        task = locked(task)
        if task.assigned_to_id != user.pk:
            raise PermissionDenied("Only the assigned waiter can accept this task.")
        if task.status != Status.ASSIGNED or task.accepted_at:
            raise ValidationError("This task cannot be accepted now.")
        task.accepted_at = timezone.now()
        task.save(update_fields=["accepted_at"])
        history(task, Action.ACCEPTED, user, task.status)
        publish(task)
    return task


def start(task, *, user):
    """ASSIGNED -> IN_PROGRESS. The table shows "Cleaning in progress" while it lasts."""
    with transaction.atomic():
        task = locked(task)
        rule = TRANSITIONS.get((task.status, Status.IN_PROGRESS))
        if rule is None:
            raise ValidationError("Only assigned tasks can be started.")
        if not may_use(rule, task, user):
            raise PermissionDenied("Only the assigned waiter or a manager can start this task.")
        before = table_services.status_of(task.table)
        now = timezone.now()
        task.status, task.started_at = Status.IN_PROGRESS, now
        task.accepted_at = task.accepted_at or now
        task.save()
        history(task, Action.STARTED, user, Status.ASSIGNED)
        table_services.log_status_change(task.table, before, source=TableStatusHistory.Source.TASK, user=user,
                                         note=f"{task.task_code} started")
        publish(task)
    return task


def add_progress(task, note, *, user):
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Write a short progress note."})
    with transaction.atomic():
        task = locked(task)
        if task.status != Status.IN_PROGRESS:
            raise ValidationError("Progress notes are for tasks in progress.")
        if not (is_manager(user) or task.assigned_to_id == user.pk):
            raise PermissionDenied("Only the assigned waiter or a manager can update this task.")
        history(task, Action.PROGRESS, user, task.status, note=note)
        publish(task)
    return task


def complete(task, *, user, check, remarks=""):
    """
    IN_PROGRESS -> COMPLETED with the waiter's table check (customers, dishes,
    clean, chairs, ready). The check is saved as an inspection and decides the
    table's new condition; the table is Ready only if everything is satisfied.
    """
    with transaction.atomic():
        task = locked(task)
        rule = TRANSITIONS.get((task.status, Status.COMPLETED))
        if rule is None:
            raise ValidationError("Only tasks in progress can be completed.")
        if not may_use(rule, task, user):
            raise PermissionDenied("Only the assigned waiter or a manager can complete this task.")
        before = table_services.status_of(task.table)  # still "Cleaning in progress"
        task.status, task.completed_at = Status.COMPLETED, timezone.now()
        task.completion_remarks = (remarks or "").strip()
        task.save()
        history(task, Action.COMPLETED, user, Status.IN_PROGRESS, note=task.completion_remarks)
        inspection = inspection_services.record_inspection(
            task.table, user, source=TableInspection.Source.TASK, task=task, before=before,
            remarks=task.completion_remarks, **check,
        )
        publish(task)
        result = TableStatus(inspection.resulting_status).label
        notifications.notify_roles(
            task.restaurant, [Role.MANAGER], kind=Notification.Kind.TASK_COMPLETED,
            title=f"{task.task_code} done by {name(user)}: {task.table.label} is {result}",
            message=task.get_task_type_display() + (f". {task.completion_remarks}" if task.completion_remarks else "."),
            link=link_for(task),
        )
    return task


# --- Overdue tasks -----------------------------------------------------------------------------


def notify_overdue_tasks(restaurant):
    """Tell managers and the waiter (once per task) about tasks past their deadline."""
    now = timezone.now()
    overdue = WaiterTask.objects.for_restaurant(restaurant).overdue(now).filter(overdue_notified_at__isnull=True)
    count = 0
    for task in overdue.select_related("table", "assigned_to"):
        with transaction.atomic():
            WaiterTask.objects.filter(pk=task.pk).update(overdue_notified_at=now)
            recipients = [*notifications.staff_with_roles(restaurant, Role.MANAGER), task.assigned_to]
            notifications.notify(
                recipients, restaurant=restaurant, kind=Notification.Kind.TASK_OVERDUE,
                title=f"Overdue: {task.task_code} {task.get_task_type_display()} at {task.table.label}",
                message=f"Deadline was {timezone.localtime(task.due_at):%H:%M}; assigned to {name(task.assigned_to)}.",
                link=link_for(task),
            )
            publish(task)
        count += 1
    return count
