"""Dashboard queries. Every number on the Overview page comes from here."""
from inspections.models import Inspection
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
    return summary
