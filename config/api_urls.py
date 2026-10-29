"""
All JSON API endpoints in one place, mounted at /api/v1/.

Every view is marked login_not_required so that DRF (not the login-redirect
middleware) answers unauthenticated calls with a JSON error. Each view still
declares its own authentication and permission classes: browser calls use the
session + CSRF, the simulator uses its token.
"""
from django.contrib.auth.decorators import login_not_required
from django.urls import path

from core import api as core_api
from equipment import api as equipment_api
from monitoring import api as monitoring_api


def api_view(view_class):
    return login_not_required(view_class.as_view())


app_name = "api"

urlpatterns = [
    path("ingest/", api_view(monitoring_api.IngestView), name="ingest"),
    path("dashboard/summary/", api_view(core_api.DashboardSummaryView), name="dashboard_summary"),
    path("equipment/status/", api_view(monitoring_api.EquipmentStatusView), name="equipment_status"),
    path("equipment/lookup/", api_view(equipment_api.EquipmentLookupView), name="equipment_lookup"),
    path(
        "equipment/<slug:code>/readings/",
        api_view(monitoring_api.EquipmentReadingsView),
        name="equipment_readings",
    ),
    path("alerts/", api_view(monitoring_api.AlertListApiView), name="alerts"),
    path("alerts/<int:pk>/acknowledge/", api_view(monitoring_api.AlertAcknowledgeApiView), name="alert_acknowledge"),
    path("alerts/<int:pk>/resolve/", api_view(monitoring_api.AlertResolveApiView), name="alert_resolve"),
]
