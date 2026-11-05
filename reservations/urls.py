from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    path("", views.reservation_list, name="list"),
    path("live/", views.reservation_list_fragment, name="list_fragment"),
    path("desk/", views.desk, name="desk"),
    path("desk/live/board/", views.desk_board_fragment, name="desk_board_fragment"),
    path("desk/live/upcoming/", views.upcoming_fragment, name="upcoming_fragment"),
    path("book/", views.book, name="book"),
    path("walk-in/", views.walk_in, name="walk_in"),
    path("<int:pk>/", views.reservation_detail, name="detail"),
    path("<int:pk>/status/", views.change_status, name="change_status"),
]
