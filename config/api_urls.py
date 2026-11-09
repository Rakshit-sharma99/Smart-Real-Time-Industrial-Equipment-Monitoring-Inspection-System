"""
All JSON API endpoints in one place, mounted at /api/v1/.

Every view is marked login_not_required so that DRF (not the login-redirect
middleware) answers unauthenticated calls with a JSON error. Each view still
declares its own authentication and permission classes: browser calls use the
session + CSRF, camera services use the detection token.
"""
from django.contrib.auth.decorators import login_not_required
from django.urls import path

from cctv import api as cctv_api
from inspections import api as inspections_api
from notifications import views as notification_views
from reservations import api as reservations_api
from tables import api as tables_api


def api_view(view_class):
    return login_not_required(view_class.as_view())


app_name = "api"

urlpatterns = [
    path("detections/", api_view(inspections_api.DetectionView), name="detections"),
    path("cameras/<int:pk>/frame/", api_view(cctv_api.CameraFrameView), name="camera_frame"),
    path("cameras/<int:pk>/status/", api_view(cctv_api.CameraStatusView), name="camera_status"),
    path("floor/", api_view(tables_api.FloorApiView), name="floor"),
    path("tables/lookup/", api_view(inspections_api.TableLookupView), name="table_lookup"),
    path("reservations/", api_view(reservations_api.ReservationCreateView), name="reservations"),
    path("reservations/availability/", api_view(reservations_api.AvailabilityView), name="availability"),
    path("notifications/", api_view(notification_views.NotificationListApiView), name="notifications"),
    path("notifications/read/", api_view(notification_views.NotificationReadApiView), name="notifications_read"),
]
