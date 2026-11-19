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
    """The four state counts at the top (they add up to the total). Tables carry .status (with_status)."""
    summary = table_services.floor_summary(tables)
    return {
        "free": summary["free"],
        "seated": summary["seated"],
        "dirty": summary["dirty"],
        "unavailable": summary["unavailable"],
        "total": summary["total"],
        "seated_pct": round(summary["seated"] / summary["total"] * 100) if summary["total"] else 0,
    }


# "Needs you now", worst first. Ties: the one waiting longest first.
CRITICAL, HIGH, MEDIUM, LOW = 0, 1, 2, 3
SEVERITY_TONES = {CRITICAL: "danger", HIGH: "warning", MEDIUM: "neutral", LOW: "neutral"}


def person_name(user):
    return (user.first_name or user.username) if user else ""


def attention_items(restaurant, tables):
    """
    The manager's to-do list: one line per problem with how long it has been
    waiting and ONE action. Red items (stale, overdue) come first, then dirty
    tables nobody is cleaning, tasks not started, long sittings and tables
    without a responsible waiter.
    """
    now = timezone.now()
    active_tasks = WaiterTask.objects.for_restaurant(restaurant).active().select_related("assigned_to")
    tasks_by_table = {task.table_id: task for task in active_tasks}
    items = []

    def add(table, severity, title, since, action, who="", task=None):
        items.append({"table": table, "severity": severity, "tone": SEVERITY_TONES[severity], "title": title,
                      "since": since, "minutes": max(0, int((now - since).total_seconds() // 60)),
                      "who": who, "action": action, "task": task})

    for table in tables:
        task = tasks_by_table.get(table.pk)
        timer = getattr(table, "timer", None)
        if getattr(table, "is_stale", False):
            title = "seated, please verify" if timer["kind"] == "seated" else "dirty, please verify"
            add(table, CRITICAL, title, timer["confirmed"], "verify", person_name(table.assigned_waiter))
            continue
        if task is not None and task.due_at and task.due_at < now:
            add(table, CRITICAL, "cleaning overdue", task.due_at, "remind", person_name(task.assigned_to), task)
        elif table.status in NEEDS_CLEANING_STATUSES and task is None:
            add(table, HIGH, "dirty", timer["since"] if timer else table.status_updated_at, "assign",
                person_name(table.assigned_waiter))
        elif task is not None and task.status == WaiterTask.Status.ASSIGNED and not task.accepted_at                 and task.assigned_at and now - task.assigned_at > timedelta(minutes=5):
            add(table, MEDIUM, "task not started", task.assigned_at, "remind", person_name(task.assigned_to), task)
        if timer and timer["kind"] == "seated" and timer["level"] == "late":
            add(table, HIGH, f"seated over {restaurant.reservation_minutes} min", timer["since"], "open",
                person_name(table.assigned_waiter))
        if table.assigned_waiter_id is None:
            add(table, LOW, "has no waiter", table.created_at, "set_waiter")
    items.sort(key=lambda item: (item["severity"], item["since"]))
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
