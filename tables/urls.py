from django.urls import path

from . import views

app_name = "tables"

urlpatterns = [
    path("", views.floor, name="floor"),
    path("live/", views.floor_fragment, name="floor_fragment"),
    path("manage/", views.manage, name="manage"),
    path("new/", views.table_edit, name="create"),
    path("qr-sheet/", views.qr_sheet, name="qr_sheet"),
    path("<int:pk>/", views.table_detail, name="detail"),
    path("<int:pk>/live/", views.table_status_fragment, name="status_fragment"),
    path("<int:pk>/edit/", views.table_edit, name="edit"),
    path("<int:pk>/waiter/", views.assign_waiter, name="assign_waiter"),
    path("<int:pk>/override/", views.override, name="override"),
    path("<int:pk>/block/", views.block, name="block"),
    path("<int:pk>/active/", views.set_active, name="set_active"),
    path("<int:pk>/qr.png", views.qr_png, name="qr_png"),
]
