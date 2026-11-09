from django.urls import path

from . import views

app_name = "cctv"

urlpatterns = [
    path("", views.console, name="console"),
    path("camera/<int:pk>/", views.console, name="console_camera"),
    path("camera/<int:pk>/state/", views.console_state, name="state"),
    path("camera/<int:pk>/frame.jpg", views.frame, name="frame"),
    path("camera/<int:pk>/zones/", views.zones, name="zones"),
    path("camera/<int:pk>/zones/save/", views.save_zones, name="save_zones"),
    path("settings/", views.camera_list, name="camera_list"),
    path("settings/new/", views.camera_edit, name="camera_create"),
    path("settings/<int:pk>/", views.camera_edit, name="camera_edit"),
    path("settings/<int:pk>/delete/", views.camera_delete, name="camera_delete"),
]
