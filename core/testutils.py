"""Helpers shared by the test suites of every app."""
from django.contrib.auth.models import User

from accounts.models import Role

TEST_PASSWORD = "test-pass-123!"


def make_user(username, role=Role.INSPECTOR, **extra):
    user = User.objects.create_user(username=username, password=TEST_PASSWORD, **extra)
    user.profile.role = role
    user.profile.save()
    return user


def make_admin(username="admin_t"):
    return make_user(username, Role.ADMIN)


def make_inspector(username="inspector_t"):
    return make_user(username, Role.INSPECTOR)
