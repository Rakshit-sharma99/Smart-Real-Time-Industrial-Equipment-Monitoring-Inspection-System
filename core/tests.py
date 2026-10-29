from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role

from .testutils import make_admin, make_inspector


class AdminOnlyUrlMatrixTests(TestCase):
    """
    Table-driven role check: every URL below must be refused (403) for an
    inspector, allowed for an admin and redirect anonymous users to login.
    """

    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin("asha")
        cls.inspector = make_inspector("ivan")

    def admin_only_urls(self):
        return [
            reverse("accounts:user_list"),
            reverse("accounts:user_create"),
            reverse("accounts:user_edit", args=[self.inspector.pk]),
            reverse("accounts:user_password", args=[self.inspector.pk]),
        ]

    def test_inspector_gets_403(self):
        self.client.force_login(self.inspector)
        for url in self.admin_only_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_gets_200(self):
        self.client.force_login(self.admin)
        for url in self.admin_only_urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_redirected_to_login(self):
        for url in self.admin_only_urls():
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("accounts:login"), response["Location"])


class NavigationTests(TestCase):
    def test_admin_settings_lands_on_users(self):
        self.client.force_login(make_admin())
        self.assertRedirects(self.client.get(reverse("core:settings")), reverse("accounts:user_list"))

    def test_inspector_settings_lands_on_profile(self):
        self.client.force_login(make_inspector())
        self.assertRedirects(self.client.get(reverse("core:settings")), reverse("accounts:profile"))

    def test_inspector_does_not_see_admin_tabs(self):
        self.client.force_login(make_inspector())
        response = self.client.get(reverse("accounts:profile"))
        self.assertNotContains(response, reverse("accounts:user_list"))

    def test_dashboard_shows_role(self):
        self.client.force_login(make_admin())
        self.assertContains(self.client.get(reverse("core:dashboard")), "Admin")


class ErrorPageTests(TestCase):
    def test_custom_404(self):
        self.client.force_login(make_inspector())
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)


class SeedDemoTests(TestCase):
    def test_refuses_when_debug_false(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())

    @override_settings(DEBUG=True, DEMO_PASSWORD="Demo-pass-123")
    def test_creates_demo_users(self):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())  # running twice is safe
        self.assertEqual(User.objects.get(username="admin_demo").profile.role, Role.ADMIN)
        inspector = User.objects.get(username="inspector_demo")
        self.assertEqual(inspector.profile.role, Role.INSPECTOR)
        self.assertTrue(inspector.check_password("Demo-pass-123"))
        self.assertTrue(User.objects.filter(username="inspector2_demo").exists())
