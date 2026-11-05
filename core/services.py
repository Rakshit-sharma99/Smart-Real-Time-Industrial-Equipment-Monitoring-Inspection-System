"""Dashboard queries for the manager. Every number comes from the database."""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from inspections.models import TableInspection
from reservations.models import Reservation
from tables import services as table_services
from tasks import services as task_services
from tasks.models import WaiterTask

CHECK_INTERVAL_SECONDS = 60


def manager_kpis(restaurant, tables):
    """Floor counts plus task and booking counts. `tables` carry .status (see tables.services.with_status)."""
    summary = table_services.floor_summary(tables)
    tasks = WaiterTask.objects.for_restaurant(restaurant)
    summary["active_tasks"] = tasks.active().count()
    summary["open_tasks"] = tasks.filter(status=WaiterTask.Status.OPEN).count()
    summary["overdue_tasks"] = tasks.overdue().count()
    today = timezone.localdate()
    summary["bookings_today"] = (
        Reservation.objects.for_restaurant(restaurant).active().filter(start_at__date=today).count()
    )
    return summary


def board_panels(restaurant):
    """Side panels of the manager dashboard."""
    now = timezone.now()
    tasks = (
        WaiterTask.objects.for_restaurant(restaurant).open()
        .select_related("table", "assigned_to").order_by("status", "due_at")[:12]
    )
    bookings = (
        Reservation.objects.for_restaurant(restaurant)
        .filter(status__in=Reservation.WAITING_STATUSES, end_at__gt=now, start_at__lte=now + timedelta(hours=4))
        .select_related("table")[:10]
    )
    inspections = (
        TableInspection.objects.for_restaurant(restaurant).select_related("table", "inspector")[:8]
    )
    return {"open_tasks": tasks, "upcoming_bookings": bookings, "recent_inspections": inspections}


def run_periodic_checks(restaurant):
    """
    Time-based checks without a scheduler: run at most once a minute per
    restaurant, triggered by dashboards refreshing. Also available as the
    `check_overdue_tasks` management command for cron / Task Scheduler.
    """
    if cache.add(f"periodic-checks-{restaurant.pk}", True, CHECK_INTERVAL_SECONDS):
        task_services.notify_overdue_tasks(restaurant)
