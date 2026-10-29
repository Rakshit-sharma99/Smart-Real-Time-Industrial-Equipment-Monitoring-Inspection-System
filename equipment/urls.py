from django.urls import path

from . import views

app_name = "equipment"

urlpatterns = [
    path("", views.EquipmentListView.as_view(), name="list"),
    path("new/", views.equipment_create, name="create"),
    path("labels/", views.equipment_label_sheet, name="label_sheet"),
    path("<slug:code>/", views.equipment_detail, name="detail"),
    path("<slug:code>/edit/", views.equipment_edit, name="edit"),
    path("<slug:code>/thresholds/", views.equipment_thresholds, name="thresholds"),
    path("<slug:code>/active/", views.equipment_set_active, name="set_active"),
    path("<slug:code>/qr.png", views.equipment_qr_png, name="qr_png"),
    path("<slug:code>/label/", views.equipment_label, name="label"),
]
