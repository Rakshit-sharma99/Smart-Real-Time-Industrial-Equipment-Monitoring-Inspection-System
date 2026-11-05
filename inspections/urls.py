from django.urls import path

from . import views

app_name = "inspections"

urlpatterns = [
    path("", views.InspectionListView.as_view(), name="list"),
    path("scan/", views.scanner, name="scanner"),
    path("photos/<int:pk>/", views.photo, name="photo"),
    path("table/<int:pk>/", views.inspect, name="inspect"),
    path("<int:pk>/", views.inspection_detail, name="detail"),
]
