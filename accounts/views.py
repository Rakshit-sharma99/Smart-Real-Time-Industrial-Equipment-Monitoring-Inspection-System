from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import ListView, UpdateView

from core.permissions import AdminRequiredMixin, admin_required

from .forms import (
    LoginForm,
    ProfileForm,
    StyledPasswordChangeForm,
    StyledSetPasswordForm,
    UserCreateForm,
    UserUpdateForm,
)


class LoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class PasswordChangeView(SuccessMessageMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:profile")
    success_message = "Your password was changed."
    extra_context = {"page_title": "Change password", "settings_tab": "password"}


class ProfileView(SuccessMessageMixin, UpdateView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")
    success_message = "Profile saved."
    extra_context = {"page_title": "My profile", "settings_tab": "profile"}

    def get_object(self, queryset=None):
        return self.request.user


class UserListView(AdminRequiredMixin, ListView):
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 20
    extra_context = {"page_title": "Users & roles", "settings_tab": "users"}

    def get_queryset(self):
        users = User.objects.select_related("profile").order_by("username")
        query = self.request.GET.get("q", "").strip()
        if query:
            users = users.filter(
                Q(username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
            )
        return users


@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"User {user.username} created.")
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "page_title": "Add user", "settings_tab": "users"},
    )


@admin_required
def user_edit(request, pk):
    user = get_object_or_404(User.objects.select_related("profile"), pk=pk)
    form = UserUpdateForm(request.POST or None, instance=user, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"User {user.username} updated.")
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "edited_user": user, "page_title": f"Edit {user.username}", "settings_tab": "users"},
    )


@admin_required
def user_set_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = StyledSetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Password for {user.username} was reset.")
        return redirect("accounts:user_list")
    return render(
        request,
        "accounts/set_password.html",
        {"form": form, "edited_user": user, "page_title": f"Reset password: {user.username}", "settings_tab": "users"},
    )
