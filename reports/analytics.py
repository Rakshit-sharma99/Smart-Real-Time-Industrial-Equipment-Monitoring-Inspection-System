"""
Operational analytics for one restaurant and period.

Every figure is labelled:
  MEASURED  counted or timed directly from recorded events (timestamps, counts)
  ESTIMATE  calculated from measured data with a stated assumption
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from itertools import groupby
from statistics import mean

from django.db.models import Count, Sum
from django.utils import timezone

from inspections.models import TableInspection
from reservations.models import Reservation
from tables.models import RestaurantTable, TableStatusHistory
from tasks.models import WaiterTask

MEASURED = "measured"
ESTIMATE = "estimate"
MAX_DAYS = 31


@dataclass
class Figure:
    label: str
    value: object
    unit: str = ""
    kind: str = MEASURED
    note: str = ""


@dataclass
class OperationsReport:
    start_date: object
    end_date: object
    days: int
    figures: list = field(default_factory=list)
    waiters: list = field(default_factory=list)
    daily: list = field(default_factory=list)
    peak_hours: list = field(default_factory=list)  # [(hour, average occupied tables)]
    inspections_by_source: list = field(default_factory=list)


def period_bounds(start_date, end_date):
    """Local midnight at the start of start_date to local midnight after end_date."""
    start = timezone.make_aware(datetime.combine(start_date, time.min))
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
    return start, end


def minutes_between(earlier, later):
    return (later - earlier).total_seconds() / 60


def average(values):
    values = [v for v in values if v is not None]
    return round(mean(values), 1) if values else None


def occupied_intervals(restaurant, start, end):
    """
    [(from, to)] periods when a table was occupied, clipped to [start, end).
    Rebuilt from the audit log (each row stores is_occupied after the change).
    """
    rows = (
        TableStatusHistory.objects.filter(table__restaurant=restaurant, created_at__lt=end)
        .order_by("table_id", "created_at", "id")
        .values_list("table_id", "created_at", "is_occupied")
    )
    intervals = []
    for _table_id, events in groupby(rows, key=lambda row: row[0]):
        occupied_since = None
        for _, at, occupied in events:
            if occupied and occupied_since is None:
                occupied_since = at
            elif not occupied and occupied_since is not None:
                intervals.append((occupied_since, at))
                occupied_since = None
        if occupied_since is not None:
            intervals.append((occupied_since, min(end, timezone.now())))
    clipped = [(max(a, start), min(b, end)) for a, b in intervals]
    return [(a, b) for a, b in clipped if b > a]


def hourly_occupancy(intervals, days):
    """Average number of occupied tables for each hour of the day (local time)."""
    minutes_per_hour = defaultdict(float)
    for begin, finish in intervals:
        cursor = timezone.localtime(begin)
        finish = timezone.localtime(finish)
        while cursor < finish:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            step_end = min(next_hour, finish)
            minutes_per_hour[cursor.hour] += minutes_between(cursor, step_end)
            cursor = step_end
    return [(hour, round(minutes_per_hour[hour] / (60 * days), 2)) for hour in range(24)]


def operations_report(restaurant, start_date, end_date):
    days = (end_date - start_date).days + 1
    start, end = period_bounds(start_date, end_date)
    report = OperationsReport(start_date=start_date, end_date=end_date, days=days)

    bookings = Reservation.objects.for_restaurant(restaurant)
    seated = list(bookings.filter(seated_at__gte=start, seated_at__lt=end))
    tasks = list(
        WaiterTask.objects.for_restaurant(restaurant).filter(completed_at__gte=start, completed_at__lt=end)
        .select_related("assigned_to")
    )
    inspections = TableInspection.objects.for_restaurant(restaurant).filter(created_at__gte=start, created_at__lt=end)
    active_tables = RestaurantTable.objects.filter(restaurant=restaurant, is_active=True).count()
    intervals = occupied_intervals(restaurant, start, end)
    occupied_minutes = sum(minutes_between(a, b) for a, b in intervals)
    open_minutes = days * restaurant.open_hours_per_day * 60

    cleaning = [minutes_between(t.started_at, t.completed_at) for t in tasks if t.started_at]
    response = [minutes_between(t.assigned_at, t.started_at) for t in tasks if t.assigned_at and t.started_at]
    late = [t for t in tasks if t.due_at and t.completed_at > t.due_at]
    waiting = [r.waiting_minutes for r in seated if r.source == Reservation.Source.BOOKING]
    utilization = occupied_minutes / (active_tables * open_minutes) * 100 if active_tables and open_minutes else None

    report.figures = [
        Figure("Parties seated (tables served)", len(seated)),
        Figure("Guests seated", sum(r.party_size for r in seated)),
        Figure("Walk-ins", sum(1 for r in seated if r.source == Reservation.Source.WALK_IN)),
        Figure("Cancellations", bookings.filter(cancelled_at__gte=start, cancelled_at__lt=end).count()),
        Figure("No-shows", bookings.filter(status=Reservation.Status.NO_SHOW, start_at__gte=start, start_at__lt=end).count()),
        Figure("Average reservation waiting time", average(waiting), "min", note="arrival to seating"),
        Figure("Cleaning tasks completed", len(tasks)),
        Figure("Average cleaning time", average(cleaning), "min", note="task started to completed"),
        Figure("Average task response time", average(response), "min", note="assigned to started"),
        Figure("Tasks completed late", len(late), note="completed after the deadline"),
        Figure("Average delay of late tasks", average(minutes_between(t.due_at, t.completed_at) for t in late), "min"),
        Figure("Tasks overdue right now", WaiterTask.objects.for_restaurant(restaurant).overdue().count()),
        Figure("Inspections recorded", inspections.count()),
        Figure("Table utilisation", round(utilization, 1) if utilization is not None else None, "%", ESTIMATE,
               note=f"occupied table-minutes ÷ ({active_tables} tables × {restaurant.open_hours_per_day:g} opening hours/day); "
                    "assumes every table is usable all opening hours"),
    ]
    report.peak_hours = hourly_occupancy(intervals, days)

    by_waiter = defaultdict(list)
    for task in tasks:
        by_waiter[task.assigned_to].append(task)
    assigned_counts = dict(
        WaiterTask.objects.for_restaurant(restaurant).filter(assigned_at__gte=start, assigned_at__lt=end)
        .values_list("assigned_to").annotate(n=Count("id")).values_list("assigned_to", "n")
    )
    for waiter, done in sorted(by_waiter.items(), key=lambda item: -len(item[1])):
        report.waiters.append({
            "name": (waiter.get_full_name() or waiter.username) if waiter else "Unassigned",
            "assigned": assigned_counts.get(waiter.pk if waiter else None, 0),
            "completed": len(done),
            "avg_minutes": average(minutes_between(t.started_at, t.completed_at) for t in done if t.started_at),
            "late": sum(1 for t in done if t.due_at and t.completed_at > t.due_at),
        })

    for offset in range(days):
        day = start_date + timedelta(days=offset)
        day_start, day_end = period_bounds(day, day)
        day_tasks = [t for t in tasks if day_start <= t.completed_at < day_end]
        report.daily.append({
            "date": day,
            "seated": sum(1 for r in seated if day_start <= r.seated_at < day_end),
            "guests": sum(r.party_size for r in seated if day_start <= r.seated_at < day_end),
            "tasks": len(day_tasks),
            "avg_cleaning": average(minutes_between(t.started_at, t.completed_at) for t in day_tasks if t.started_at),
            "inspections": inspections.filter(created_at__gte=day_start, created_at__lt=day_end).count(),
            "cancelled": bookings.filter(cancelled_at__gte=day_start, cancelled_at__lt=day_end).count(),
        })

    sources = dict(TableInspection.Source.choices)
    report.inspections_by_source = [
        (sources[row["source"]], row["n"]) for row in inspections.values("source").annotate(n=Count("id")).order_by("source")
    ]
    return report


def weekly_totals(report):
    """Totals of the daily rows (used as the weekly summary when the period is a week)."""
    return {
        "seated": sum(d["seated"] for d in report.daily),
        "guests": sum(d["guests"] for d in report.daily),
        "tasks": sum(d["tasks"] for d in report.daily),
        "inspections": sum(d["inspections"] for d in report.daily),
        "cancelled": sum(d["cancelled"] for d in report.daily),
    }


__all__ = ["operations_report", "weekly_totals", "MEASURED", "ESTIMATE", "MAX_DAYS", "Sum"]
