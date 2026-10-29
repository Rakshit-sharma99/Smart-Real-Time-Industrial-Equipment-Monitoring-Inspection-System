from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.permissions import get_role, is_admin
from core.testutils import TEST_PASSWORD, make_admin, make_inspector

from .models import Role


class ProfileSignalTests(TestCase):
    def test_new_user_gets_inspector_profile(self):
        user = User.objects.create_user("plain", password="x")
        self.assertEqual(user.profile.role, Role.INSPECTOR)

    def test_superuser_counts_as_admin(self):
        user = User.objects.create_superuser("root", password="x")
        self.assertEqual(get_role(user), Role.ADMIN)
        self.assertTrue(is_admin(user))


class LoginLogoutTests(TestCase):
    def setUp(self):
        self.user = make_inspector("ivan")

    def test_login_page_renders(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")

    def test_login_with_valid_credentials_redirects_to_dashboard(self):
        response = self.client.post(
            reverse("accounts:login"), {"username": "ivan", "password": TEST_PASSWORD}
        )
        self.assertRedirects(response, reverse("core:dashboard"))

    def test_login_with_wrong_password_fails(self):
        response = self.client.post(reverse("accounts:login"), {"username": "ivan", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            reverse("accounts:login"), {"username": "ivan", "password": TEST_PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivated_user_loses_existing_session(self):
        self.client.force_login(self.user)
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/")


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_admin("asha")
        self.client.force_login(self.admin)

    def test_admin_creates_user_with_role(self):
        response = self.client.post(
            reverse("accounts:user_create"),
            {
                "username": "newbie",
                "first_name": "New",
                "last_name": "User",
                "email": "n@example.com",
                "role": Role.ADMIN,
                "password1": "Sup3r-secret-pass",
                "password2": "Sup3r-secret-pass",
            },
        )
        self.assertRedirects(response, reverse("accounts:user_list"))
        self.assertEqual(User.objects.get(username="newbie").profile.role, Role.ADMIN)

    def test_admin_cannot_deactivate_self(self):
        response = self.client.post(
            reverse("accounts:user_edit", args=[self.admin.pk]),
            {"first_name": "", "last_name": "", "email": "", "role": Role.ADMIN},  # is_active omitted = False
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_inspector_cannot_open_user_management(self):
        self.client.force_login(make_inspector("ivan"))
        response = self.client.get(reverse("accounts:user_list"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "You don't have access", status_code=403)
