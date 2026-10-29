from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    # Django's LogoutView only accepts POST, so a link or image tag cannot log you out.
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/new/", views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<int:pk>/password/", views.user_set_password, name="user_password"),
]
