from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_home, name="home"),
    path("inspection/<int:pk>.pdf", views.inspection_report, name="inspection_pdf"),
    path("equipment/<slug:code>/health.pdf", views.health_report, name="health_pdf"),
]
