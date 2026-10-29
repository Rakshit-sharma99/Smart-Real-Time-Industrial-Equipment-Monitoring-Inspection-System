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
from equipment.models import Equipment

DEMO_USERS = [
    # username, first name, last name, role
    ("admin_demo", "Asha", "Admin", Role.ADMIN),
    ("inspector_demo", "Ivan", "Inspector", Role.INSPECTOR),
    ("inspector2_demo", "Isha", "Inspector", Role.INSPECTOR),
]

T = Equipment.EquipmentType
DEMO_EQUIPMENT = [
    # code, name, type, location, extra fields
    ("EQ-001", "Air Compressor AC-1", T.AIR_COMPRESSOR, "Compressor House", {"pres_warn": "9.00", "pres_crit": "11.00"}),
    ("EQ-002", "Hydraulic Pump HP-2", T.HYDRAULIC_PUMP, "Press Shop", {}),
    ("EQ-003", "CNC Milling Centre", T.CNC_MACHINE, "Machining Bay B", {"sim_mode": Equipment.SimMode.DRIFT}),
    ("EQ-004", "Steam Boiler B-1", T.BOILER, "Boiler House", {"temp_warn": "95.00", "temp_crit": "110.00"}),
    ("EQ-005", "Conveyor Drive Motor", T.CONVEYOR_MOTOR, "Packing Line 1", {}),
    ("EQ-006", "Cooling Tower CT-1", T.COOLING_TOWER, "Utility Yard", {"is_active": False}),
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
            equipment = self.seed_equipment()

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Users: {', '.join(users)} (password from DEMO_PASSWORD in .env)")
        self.stdout.write(f"  Equipment: {len(equipment)} machines")

    # --- steps -----------------------------------------------------------------

    def reset(self):
        """Delete ALL equipment-related data and the demo users (this is a demo database)."""
        Equipment.objects.all().delete()
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

    def seed_equipment(self):
        equipment = {}
        for code, name, equipment_type, location, extra in DEMO_EQUIPMENT:
            fields = {"name": name, "equipment_type": equipment_type, "location": location, **extra}
            machine, _ = Equipment.objects.get_or_create(equipment_code=code, defaults=fields)
            equipment[code] = machine
        return equipment
