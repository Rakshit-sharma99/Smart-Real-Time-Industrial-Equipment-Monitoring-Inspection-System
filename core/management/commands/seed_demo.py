"""
Create demo data for local development and the viva demonstration.

    python manage.py seed_demo           # create anything that is missing
    python manage.py seed_demo --reset   # wipe demo data first, then recreate

Refuses to run when DEBUG=False so it can never touch a production database.
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from equipment.metrics import VIBRATION
from equipment.models import Equipment
from equipment.services import recompute_health
from monitoring.models import Alert, SensorReading, SimulatorSettings, Source
from monitoring.services import AUTO_RESOLVE_NOTE
from monitoring.simulation import SensorSimulator

BACKFILL_HOURS = 24
BACKFILL_STEP_MINUTES = 5

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
            readings = self.seed_readings(equipment)
            self.seed_alert_history(equipment)
            SimulatorSettings.load()

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Users: {', '.join(users)} (password from DEMO_PASSWORD in .env)")
        self.stdout.write(f"  Equipment: {len(equipment)} machines")
        self.stdout.write(f"  Readings backfilled: {readings}")
        self.stdout.write("  Machines show OFFLINE until you start: python manage.py simulate_sensors")

    # --- steps -----------------------------------------------------------------

    def reset(self):
        """Delete ALL equipment-related data and the demo users (this is a demo database)."""
        Equipment.objects.all().delete()  # cascades to readings and alerts
        SimulatorSettings.objects.all().delete()
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

    def seed_readings(self, equipment):
        """
        24 h of history at 5-minute steps so charts are not empty. Generated
        with the simulator in NORMAL mode and saved directly (no alerts).
        Machines that already have readings are skipped.
        """
        simulator = SensorSimulator(rng=random.Random(42))
        now = timezone.now()
        steps = BACKFILL_HOURS * 60 // BACKFILL_STEP_MINUTES
        created = 0
        for machine in equipment.values():
            if machine.readings.exists():
                continue
            # The deactivated machine gets history that ends a week ago.
            end = now if machine.is_active else now - timedelta(days=7)
            rows = []
            for step in range(steps, 0, -1):
                values = simulator.next_values(machine, noise_pct=2.0, mode=Equipment.SimMode.NORMAL)
                rows.append(SensorReading(
                    equipment=machine,
                    recorded_at=end - timedelta(minutes=BACKFILL_STEP_MINUTES * (step - 1)),
                    source=Source.SIMULATED,
                    **values,
                ))
            SensorReading.objects.bulk_create(rows)
            created += len(rows)
            machine.last_reading_at = rows[-1].recorded_at
            machine.save(update_fields=["last_reading_at", "updated_at"])
            recompute_health(machine)
        return created

    def seed_alert_history(self, equipment):
        """One resolved alert from yesterday so the alert history is not empty."""
        machine = equipment["EQ-003"]
        if machine.alerts.exists():
            return
        start = timezone.now() - timedelta(hours=20)
        Alert.objects.create(
            equipment=machine,
            metric=VIBRATION.key,
            severity=Alert.Severity.CRITICAL,
            status=Alert.Status.RESOLVED,
            first_value=Decimal("7.40"),
            last_value=Decimal("5.10"),
            peak_value=Decimal("11.32"),
            threshold_value=machine.vib_crit,
            description=(
                f"Escalated to CRITICAL. {VIBRATION.label} reached 11.32 {VIBRATION.unit} on "
                f"{machine.equipment_code} ({machine.name}), at or above the critical limit of "
                f"{machine.vib_crit} {VIBRATION.unit}. Source: SIMULATED sensor data "
                "(demo reading, not a real emergency)."
            ),
            created_at=start,
            last_seen_at=start + timedelta(minutes=6),
            escalated_at=start + timedelta(minutes=4),
            resolved_at=start + timedelta(minutes=9),
            resolution_notes=AUTO_RESOLVE_NOTE,
        )
