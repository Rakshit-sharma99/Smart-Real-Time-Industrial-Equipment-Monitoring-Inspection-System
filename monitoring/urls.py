from django.urls import path

from . import views

app_name = "monitoring"

urlpatterns = [
    path("live/", views.live_monitoring, name="live"),
    path("settings/simulator/", views.simulator_settings, name="simulator_settings"),
    path("settings/simulator/<slug:code>/mode/", views.set_sim_mode, name="set_sim_mode"),
]
