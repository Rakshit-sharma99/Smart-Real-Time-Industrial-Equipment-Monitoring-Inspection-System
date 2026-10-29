"""Dashboard queries. Every number on the Overview page comes from here."""
from django.db.models import Count
from django.utils import timezone

from inspections.models import Inspection
from maintenance.models import MaintenanceTask
from monitoring.broadcast import summary_counts


def dashboard_summary(user):
    """
    KPI counts for one user. Equipment health counts and open alerts are the
    same for everyone; inspection counts only include what the user may see
    (admins: all, inspectors: their own).
    """
    summary = summary_counts()
    inspections = Inspection.objects.visible_to(user)
    summary["pending_inspections"] = inspections.open().count()
    summary["failed_inspections"] = inspections.filter(overall_result=Inspection.Result.FAIL).count()
    summary["open_tasks"] = MaintenanceTask.objects.visible_to(user).open().count()
    return summary


def task_summary(user):
    """Task counts per status for the tasks the user may see, plus how many open tasks are overdue."""
    tasks = MaintenanceTask.objects.visible_to(user)
    counts = dict(tasks.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    rows = [(label, value, counts.get(value, 0)) for value, label in MaintenanceTask.Status.choices]
    overdue = tasks.open().filter(due_date__lt=timezone.localdate()).count()
    return rows, overdue
