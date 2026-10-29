from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("", views.TaskListView.as_view(), name="list"),
    path("new/", views.task_create, name="create"),
    path("<int:pk>/", views.task_detail, name="detail"),
    path("<int:pk>/status/", views.task_change_status, name="change_status"),
]
