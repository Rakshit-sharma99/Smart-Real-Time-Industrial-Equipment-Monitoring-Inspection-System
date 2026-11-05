"""Factories shared by the test suites of every app."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import Role
from tables.models import Restaurant, RestaurantSection, RestaurantTable

TEST_PASSWORD = "test-pass-123!"


def make_restaurant(name="Test Bistro", slug="test-bistro"):
    restaurant = Restaurant.objects.create(name=name, slug=slug)
    RestaurantSection.objects.create(restaurant=restaurant, name="Main hall", sort_order=1)
    return restaurant


def make_staff(username, role, restaurant, **extra):
    user = User.objects.create_user(username=username, password=TEST_PASSWORD, **extra)
    user.profile.role = role
    user.profile.restaurant = restaurant
    user.profile.save()
    return user


def make_manager(restaurant, username="manager_t"):
    return make_staff(username, Role.MANAGER, restaurant)


def make_waiter(restaurant, username="waiter_t"):
    return make_staff(username, Role.WAITER, restaurant)


def make_receptionist(restaurant, username="reception_t"):
    return make_staff(username, Role.RECEPTIONIST, restaurant)


def make_table(restaurant, number=1, capacity=4, **extra):
    section = extra.pop("section", None) or restaurant.sections.first()
    return RestaurantTable.objects.create(restaurant=restaurant, section=section, number=number, capacity=capacity, **extra)


def at(hours=0, minutes=0):
    """A time relative to now, e.g. at(hours=2) = in two hours."""
    return timezone.now() + timedelta(hours=hours, minutes=minutes)


CLEAN_CHECK = {
    "customers_present": False, "plates_present": False, "glasses_present": False, "leftovers_present": False,
    "table_clean": True, "chairs_arranged": True, "ready_for_next": True,
}
GUESTS_LEFT_CHECK = {
    "customers_present": False, "plates_present": True, "glasses_present": True, "leftovers_present": False,
    "table_clean": False, "chairs_arranged": False, "ready_for_next": False,
}
