from django.urls import path

from . import alert_views

app_name = "alerts"

urlpatterns = [
    path("", alert_views.AlertListView.as_view(), name="list"),
    path("<int:pk>/", alert_views.alert_detail, name="detail"),
    path("<int:pk>/acknowledge/", alert_views.alert_acknowledge, name="acknowledge"),
    path("<int:pk>/resolve/", alert_views.alert_resolve, name="resolve"),
]
