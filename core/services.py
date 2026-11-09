"""Manager Overview queries. Every number comes from the database."""
from datetime import timedelta
from itertools import groupby

from django.core.cache import cache
from django.utils import timezone

from cctv.models import Camera
from reservations.models import Reservation
from tables import services as table_services
from tables.models import NEEDS_CLEANING_STATUSES, TableStatus
from tasks import services as task_services
from tasks.models import WaiterTask

CHECK_INTERVAL_SECONDS = 60


def overview_stats(tables):
    """The four numbers at the top: tables carry .status (tables.services.with_status)."""
    summary = table_services.floor_summary(tables)
    return {
        "occupied": summary["occupied"],
        "available": summary["ready"],
        "needs_cleaning": summary["needs_cleaning"] + summary["cleaning"],
        "reserved": summary["reserved"],
    }


def attention_items(restaurant, tables):
    """
    Tables that need the manager to act right now, each with the reason and
    the action: dirty tables without an active task (assign a waiter), and
    tasks past their deadline (chase the waiter).
    """
    now = timezone.now()
    active_tasks = WaiterTask.objects.for_restaurant(restaurant).active().select_related("assigned_to")
    tasks_by_table = {task.table_id: task for task in active_tasks}
    items = []
    for table in tables:
        task = tasks_by_table.get(table.pk)
        if table.status in NEEDS_CLEANING_STATUSES and task is None:
            reason = "Dishes remaining" if table.status == TableStatus.VACANT_NEEDS_CLEARING else "Cleaning required"
            items.append({"table": table, "reason": reason, "action": "assign", "since": table.status_updated_at})
        elif task is not None and task.due_at and task.due_at < now:
            items.append({"table": table, "reason": f"Cleaning overdue · {task.assigned_to.get_full_name() or task.assigned_to.username}",
                          "action": "task", "task": task, "since": task.due_at})
        elif task is not None and task.status == WaiterTask.Status.ASSIGNED and not task.accepted_at \
                and task.assigned_at and now - task.assigned_at > timedelta(minutes=5):
            items.append({"table": table, "reason": f"Task not started · {task.assigned_to.get_full_name() or task.assigned_to.username}",
                          "action": "task", "task": task, "since": task.assigned_at})
    items.sort(key=lambda item: item["since"])
    return items


def floor_by_section(tables):
    ordered = sorted((t for t in tables if t.is_active), key=lambda t: (t.section.sort_order, t.section.name, t.number))
    return [(section, list(rows)) for section, rows in groupby(ordered, key=lambda t: t.section)]


def side_panels(restaurant):
    now = timezone.now()
    return {
        "active_tasks": WaiterTask.objects.for_restaurant(restaurant).active()
        .select_related("table", "assigned_to").order_by("due_at")[:6],
        "upcoming_bookings": Reservation.objects.for_restaurant(restaurant)
        .filter(status__in=Reservation.WAITING_STATUSES, end_at__gt=now, start_at__lte=now + timedelta(hours=4))
        .select_related("table")[:5],
        "cameras": list(Camera.objects.filter(restaurant=restaurant, is_active=True)),
    }


def run_periodic_checks(restaurant):
    """
    Time-based checks without a scheduler: run at most once a minute per
    restaurant, triggered by dashboards refreshing. Also available as the
    `check_overdue_tasks` management command for cron / Task Scheduler.
    """
    if cache.add(f"periodic-checks-{restaurant.pk}", True, CHECK_INTERVAL_SECONDS):
        task_services.notify_overdue_tasks(restaurant)
