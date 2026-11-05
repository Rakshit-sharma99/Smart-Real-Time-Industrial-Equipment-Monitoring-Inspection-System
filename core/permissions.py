"""
Who may do what.

Three roles - Manager, Waiter, Receptionist - and every staff member works for
one restaurant. Access is always enforced on the server: views use
RoleRequiredMixin / @role_required, and every query is limited to the user's
restaurant (and for waiters to their own tasks and inspections). Hiding a
button in a template is only a convenience, never the protection.
"""
from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from accounts.models import Role, StaffProfile
from tables.models import Restaurant

ALL_ROLES = (Role.MANAGER, Role.WAITER, Role.RECEPTIONIST)


def get_role(user):
    """The user's Role, or None (anonymous, or no profile). Superusers count as Manager."""
    if user is None or not user.is_authenticated:
        return None
    if user.is_superuser:
        return Role.MANAGER
    try:
        return user.profile.role
    except StaffProfile.DoesNotExist:
        return None


def get_restaurant(user):
    """The restaurant the user works for. A superuser without one gets the first restaurant."""
    if user is None or not user.is_authenticated:
        return None
    try:
        restaurant = user.profile.restaurant
    except StaffProfile.DoesNotExist:
        restaurant = None
    if restaurant is None and user.is_superuser:
        restaurant = Restaurant.objects.order_by("pk").first()
    return restaurant


def is_manager(user):
    return get_role(user) == Role.MANAGER


def is_waiter(user):
    return get_role(user) == Role.WAITER


def is_receptionist(user):
    return get_role(user) == Role.RECEPTIONIST


def check_staff(request, roles):
    """Raise PermissionDenied unless the user has one of `roles` and belongs to a restaurant."""
    if get_role(request.user) not in roles:
        raise PermissionDenied("Your role does not allow this action.")
    if getattr(request, "restaurant", None) is None:
        raise PermissionDenied("Your account is not linked to a restaurant.")


class RoleRequiredMixin(LoginRequiredMixin):
    """Class-based views: only staff whose role is in `allowed_roles` get in."""

    allowed_roles = ALL_ROLES

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        check_staff(request, self.allowed_roles)
        return super().dispatch(request, *args, **kwargs)


class ManagerRequiredMixin(RoleRequiredMixin):
    allowed_roles = (Role.MANAGER,)


def role_required(*roles):
    """Function views: @role_required(Role.MANAGER, Role.RECEPTIONIST)."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            check_staff(request, roles)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


staff_required = role_required(*ALL_ROLES)
manager_required = role_required(Role.MANAGER)
booking_staff_required = role_required(Role.MANAGER, Role.RECEPTIONIST)
floor_staff_required = role_required(Role.MANAGER, Role.WAITER)


# --- Django REST Framework ----------------------------------------------------------


class IsRestaurantStaff(BasePermission):
    message = "Only restaurant staff can use this endpoint."
    roles = ALL_ROLES

    def has_permission(self, request, view):
        return get_role(request.user) in self.roles and getattr(request, "restaurant", None) is not None


class IsManager(IsRestaurantStaff):
    message = "Only managers can do this."
    roles = (Role.MANAGER,)


class IsBookingStaff(IsRestaurantStaff):
    message = "Only managers and receptionists can manage reservations."
    roles = (Role.MANAGER, Role.RECEPTIONIST)
