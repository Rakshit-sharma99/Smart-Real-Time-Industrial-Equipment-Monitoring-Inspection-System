"""
Create demo data for local development and the viva demonstration.

    python manage.py seed_demo           # create anything that is missing
    python manage.py seed_demo --reset   # wipe demo data first, then recreate

Refuses to run when DEBUG=False so it can never touch a production database.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role

DEMO_USERS = [
    # username, first name, last name, role
    ("admin_demo", "Asha", "Admin", Role.ADMIN),
    ("inspector_demo", "Ivan", "Inspector", Role.INSPECTOR),
    ("inspector2_demo", "Isha", "Inspector", Role.INSPECTOR),
]


class Command(BaseCommand):
    help = "Create demo users, equipment, readings, inspections and tasks (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true", help="Delete existing demo data before seeding."
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG=True (it is for local demos).")
        if not settings.DEMO_PASSWORD:
            raise CommandError("DEMO_PASSWORD is empty. Set it in .env first.")

        with transaction.atomic():
            if options["reset"]:
                self.reset()
            users = self.seed_users()

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Users: {', '.join(users)} (password from DEMO_PASSWORD in .env)")

    # --- steps -----------------------------------------------------------------

    def reset(self):
        User.objects.filter(username__in=[u[0] for u in DEMO_USERS]).delete()
        self.stdout.write("Existing demo data deleted.")

    def seed_users(self):
        users = {}
        for username, first_name, last_name, role in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": f"{username}@example.com",
                },
            )
            if created:
                user.set_password(settings.DEMO_PASSWORD)
                user.save()
            user.profile.role = role
            user.profile.save()
            users[username] = user
        return users
