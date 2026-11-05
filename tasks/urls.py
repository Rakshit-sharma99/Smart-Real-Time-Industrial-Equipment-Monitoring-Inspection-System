from django.urls import path

from . import views

app_name = "tasks"

urlpatterns = [
    path("", views.task_list, name="list"),
    path("live/", views.task_list_fragment, name="list_fragment"),
    path("mine/", views.waiter_home, name="waiter_home"),
    path("mine/live/", views.waiter_fragment, name="waiter_fragment"),
    path("new/", views.task_create, name="create"),
    path("<int:pk>/", views.task_detail, name="detail"),
    path("<int:pk>/assign/", views.assign, name="assign"),
    path("<int:pk>/unassign/", views.unassign, name="unassign"),
    path("<int:pk>/priority/", views.priority, name="priority"),
    path("<int:pk>/accept/", views.accept, name="accept"),
    path("<int:pk>/start/", views.start, name="start"),
    path("<int:pk>/progress/", views.progress, name="progress"),
    path("<int:pk>/complete/", views.complete, name="complete"),
]
