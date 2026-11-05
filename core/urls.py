from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("manager/live/kpis/", views.fragment_kpis, name="fragment_kpis"),
    path("manager/live/panels/", views.fragment_panels, name="fragment_panels"),
    path("settings/", views.settings_home, name="settings"),
    path("settings/restaurant/", views.restaurant_settings, name="restaurant_settings"),
    path("settings/sections/", views.sections, name="sections"),
    path("settings/sections/new/", views.section_edit, name="section_create"),
    path("settings/sections/<int:pk>/", views.section_edit, name="section_edit"),
]
