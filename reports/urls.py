from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_home, name="home"),
    path("operations.pdf", views.operations_pdf, name="operations_pdf"),
    path("table/<int:pk>.pdf", views.table_history_pdf, name="table_pdf"),
]
