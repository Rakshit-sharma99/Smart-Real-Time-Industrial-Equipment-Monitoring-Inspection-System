from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User
from django.contrib.messages.views import SuccessMessageMixin
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from core.permissions import manager_required, staff_required

from .forms import (LoginForm, ProfileForm, StaffCreateForm, StaffUpdateForm, StyledPasswordChangeForm,
                    StyledSetPasswordForm)


class LoginView(auth_views.LoginView):
    """
    Django's login, plus brute-force protection: after LOGIN_MAX_FAILED_ATTEMPTS
    wrong passwords for one username from one IP address, further attempts are
    refused for LOGIN_LOCKOUT_SECONDS.
    """

    template_name = "registration/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    @staticmethod
    def attempts_key(request):
        username = request.POST.get("username", "").strip().lower()[:150]
        return f"login-failures:{request.META.get('REMOTE_ADDR', '')}:{username}"

    def post(self, request, *args, **kwargs):
        if cache.get(self.attempts_key(request), 0) >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            context = self.get_context_data(form=self.get_form_class()(request), locked_out=True)
            return self.render_to_response(context, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self.attempts_key(self.request)
        cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
        cache.incr(key)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self.attempts_key(self.request))
        return super().form_valid(form)


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


# --- Staff management (managers, own restaurant only) ------------------------------------------


def staff_of(restaurant):
    return User.objects.filter(profile__restaurant=restaurant).select_related("profile")


@manager_required
def staff_list(request):
    staff = staff_of(request.restaurant).order_by("profile__role", "first_name", "username")
    return render(request, "accounts/staff_list.html", {"staff": staff, "page_title": "Staff"})


@manager_required
def staff_create(request):
    form = StaffCreateForm(request.POST or None, restaurant=request.restaurant)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"{user.username} added as {user.profile.get_role_display()}.")
        return redirect("accounts:staff_list")
    return render(request, "accounts/staff_form.html", {"form": form, "page_title": "Add staff member"})


@manager_required
def staff_edit(request, pk):
    member = get_object_or_404(staff_of(request.restaurant), pk=pk)
    form = StaffUpdateForm(request.POST or None, instance=member, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{member.username} updated.")
        return redirect("accounts:staff_list")
    return render(request, "accounts/staff_form.html", {"form": form, "member": member, "page_title": f"Edit {member.username}"})


@manager_required
def staff_password(request, pk):
    member = get_object_or_404(staff_of(request.restaurant), pk=pk)
    form = StyledSetPasswordForm(member, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Password for {member.username} was reset.")
        return redirect("accounts:staff_list")
    return render(request, "accounts/staff_password.html", {"form": form, "member": member,
                                                             "page_title": f"Reset password: {member.username}"})


profile = staff_required(ProfileView.as_view())
password_change = staff_required(PasswordChangeView.as_view())
