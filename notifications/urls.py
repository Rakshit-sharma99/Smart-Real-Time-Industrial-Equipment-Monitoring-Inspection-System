from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("menu/", views.menu_fragment, name="menu"),
    path("read-all/", views.mark_all_read, name="mark_all_read"),
    path("<int:pk>/open/", views.open_notification, name="open"),
]
