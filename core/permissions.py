"""
Role checks used by every app.

Access is always enforced on the server: views use RoleRequiredMixin /
@role_required, and querysets use .visible_to(user) for object-level checks.
Hiding a button in a template is only a convenience, never the protection.
"""
from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.models import Role, UserProfile


def get_role(user):
    """Return the user's Role, or None for anonymous users. Superusers count as Admin."""
    if user is None or not user.is_authenticated:
        return None
    if user.is_superuser:
        return Role.ADMIN
    try:
        return user.profile.role
    except UserProfile.DoesNotExist:
        return Role.INSPECTOR


def is_admin(user):
    return get_role(user) == Role.ADMIN


class RoleRequiredMixin(LoginRequiredMixin):
    """Class-based view mixin: only users whose role is in `allowed_roles` get in."""

    allowed_roles = (Role.ADMIN, Role.INSPECTOR)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if get_role(request.user) not in self.allowed_roles:
            raise PermissionDenied("Your role does not allow this action.")
        return super().dispatch(request, *args, **kwargs)


class AdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = (Role.ADMIN,)


def role_required(*roles):
    """Function-view decorator: @role_required(Role.ADMIN)."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if get_role(request.user) not in roles:
                raise PermissionDenied("Your role does not allow this action.")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


admin_required = role_required(Role.ADMIN)


class IsAdminRole(BasePermission):
    """DRF permission: logged-in user with the Admin role."""

    message = "Only admins can do this."

    def has_permission(self, request, view):
        return is_admin(request.user)
