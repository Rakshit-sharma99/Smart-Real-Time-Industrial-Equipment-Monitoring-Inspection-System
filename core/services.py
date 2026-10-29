"""Dashboard queries. Every number on the Overview page comes from here."""
from monitoring.broadcast import summary_counts


def dashboard_summary(user):
    """
    KPI counts for one user: equipment health counts and open alerts are the
    same for everyone; inspection and task counts depend on what the user may see.
    """
    return summary_counts()
