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
from inspections import services as inspection_services
from inspections.models import Defect, Inspection, InspectionResponse, InspectionTemplate
from maintenance import services as maintenance_services
from maintenance.models import MaintenanceTask
from monitoring.models import Alert, SensorReading, SimulatorSettings, Source
from monitoring.services import AUTO_RESOLVE_NOTE
from monitoring.simulation import SensorSimulator

GENERAL_CHECKLIST = [
    "Guards and safety covers are in place and secure",
    "No unusual noise or vibration while running",
    "No oil, coolant or air leaks visible",
    "Belts, couplings and fasteners are tight and undamaged",
    "Emergency stop works and is clearly labelled",
    "Cables and electrical panels are undamaged and closed",
    "Lubrication levels are within the marked range",
    "Area around the machine is clean and unobstructed",
]
BOILER_CHECKLIST = [
    "Pressure gauge reads within the normal operating range",
    "Safety relief valve is free of corrosion and its seal is intact",
    "Water level glass is clean and shows the correct level",
    "No leaks at flanges, valves or joints",
    "Burner flame is stable (no flicker or soot)",
    "Insulation and lagging are intact",
]

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
    ("EQ-001", "Air Compressor AC-1", T.AIR_COMPRESSOR, "Compressor House", {"pres_warn": Decimal("9.00"), "pres_crit": Decimal("11.00")}),
    ("EQ-002", "Hydraulic Pump HP-2", T.HYDRAULIC_PUMP, "Press Shop", {}),
    ("EQ-003", "CNC Milling Centre", T.CNC_MACHINE, "Machining Bay B", {"sim_mode": Equipment.SimMode.DRIFT}),
    ("EQ-004", "Steam Boiler B-1", T.BOILER, "Boiler House", {"temp_warn": Decimal("95.00"), "temp_crit": Decimal("110.00")}),
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
            inspection_count = self.seed_inspections(users, equipment)
            self.seed_tasks(users, equipment)

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Users: {', '.join(users)} (password from DEMO_PASSWORD in .env)")
        self.stdout.write(f"  Equipment: {len(equipment)} machines")
        self.stdout.write(f"  Readings backfilled: {readings}")
        self.stdout.write(f"  Inspections: {inspection_count} (2 assigned to inspector_demo, 1 submitted with a defect)")
        self.stdout.write(f"  Maintenance tasks: {MaintenanceTask.objects.count()} open")
        self.stdout.write("  Machines show OFFLINE until you start: python manage.py simulate_sensors")

    # --- steps -----------------------------------------------------------------

    def reset(self):
        """Delete ALL equipment-related data and the demo users (this is a demo database)."""
        # Task and inspection records are protected, so delete them before the equipment.
        MaintenanceTask.objects.all().delete()
        Defect.objects.all().delete()
        Inspection.objects.all().delete()
        InspectionTemplate.objects.all().delete()
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

    def seed_inspections(self, users, equipment):
        """Two checklist templates, two assigned inspections and one submitted inspection with a defect."""
        general = self.make_template("General equipment inspection", "", GENERAL_CHECKLIST)
        boiler = self.make_template("Boiler & pressure vessel inspection", Equipment.EquipmentType.BOILER, BOILER_CHECKLIST)
        if Inspection.objects.exists():
            return Inspection.objects.count()

        admin, inspector = users["admin_demo"], users["inspector_demo"]
        today = timezone.localdate()
        inspection_services.assign_inspection(
            equipment=equipment["EQ-001"], template=general, inspector=inspector,
            due_date=today + timedelta(days=1), created_by=admin,
        )
        inspection_services.assign_inspection(
            equipment=equipment["EQ-004"], template=boiler, inspector=inspector,
            due_date=today + timedelta(days=3), created_by=admin,
        )

        # A submitted inspection, created through the real services so the rules apply.
        done = inspection_services.assign_inspection(
            equipment=equipment["EQ-002"], template=general, inspector=inspector,
            due_date=today - timedelta(days=1), created_by=admin,
        )
        inspection_services.start_inspection(done, inspector)
        Outcome = InspectionResponse.Outcome
        for response in done.responses.all():
            if "leaks" in response.item_text:
                response.outcome = Outcome.FAIL
                response.remarks = "Hydraulic oil weeping from the pump outlet fitting; drip tray half full."
            elif "Emergency stop" in response.item_text:
                response.outcome = Outcome.NOT_APPLICABLE
                response.remarks = "E-stop is on the shared press panel, checked separately."
            else:
                response.outcome = Outcome.PASS
            response.save()
        done.remarks = "Pump running normally apart from the leak. Recommend replacing the outlet seal."
        done.save(update_fields=["remarks"])
        inspection_services.submit_inspection(done, inspector)
        # Back-date it so the history looks natural.
        yesterday = timezone.now() - timedelta(days=1)
        Inspection.objects.filter(pk=done.pk).update(
            created_at=yesterday - timedelta(days=1), started_at=yesterday - timedelta(minutes=25), submitted_at=yesterday
        )
        Defect.objects.filter(inspection=done).update(created_at=yesterday)
        return Inspection.objects.count()

    def make_template(self, name, equipment_type, items):
        template, created = InspectionTemplate.objects.get_or_create(
            name=name, defaults={"equipment_type": equipment_type}
        )
        if created:
            for order, text in enumerate(items, start=1):
                template.items.create(order=order, text=text)
        return template

    def seed_tasks(self, users, equipment):
        """One open maintenance task (the seeded defect is left for the demo's "convert to task" step)."""
        if MaintenanceTask.objects.exists():
            return
        maintenance_services.create_task(
            equipment=equipment["EQ-003"],
            title="Replace spindle coolant filter",
            description="Coolant flow looked weak during the last shift. Replace the filter cartridge and check flow.",
            priority=MaintenanceTask.Priority.MEDIUM,
            due_date=timezone.localdate() + timedelta(days=2),
            created_by=users["admin_demo"],
        )
