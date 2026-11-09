"""
Demo data for RestaurantSpy (DEBUG only).

    python manage.py seed_demo           # create anything that is missing
    python manage.py seed_demo --reset   # wipe the demo restaurant's data first

One restaurant ("The Copper Fork"), 3 sections, 20 tables, 1 manager, 4 waiters,
2 receptionists, a realistic live floor, today's bookings, one open task and a
week of history (seated bookings, completed tasks, inspections) for the reports.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from inspections import services as inspections
from inspections.models import TableInspection
from notifications.models import Notification
from reservations import services as bookings
from reservations.models import Reservation
from tables import services as tables
from tables.models import Cleanliness, Restaurant, RestaurantSection, RestaurantTable, TableStatusHistory
from tasks import services as tasks
from tasks.models import WaiterTask

SLUG = "copper-fork"
DEMO_USERS = [
    # username, first, last, role
    ("manager_demo", "Meera", "Manager", Role.MANAGER),
    ("waiter_demo", "Wasim", "Waiter", Role.WAITER),
    ("waiter2_demo", "Wren", "Waiter", Role.WAITER),
    ("waiter3_demo", "Wiktor", "Waiter", Role.WAITER),
    ("waiter4_demo", "Wanda", "Waiter", Role.WAITER),
    ("reception_demo", "Riya", "Reception", Role.RECEPTIONIST),
    ("reception2_demo", "Rohan", "Reception", Role.RECEPTIONIST),
]
SECTIONS = [("Main hall", "Ground floor"), ("Window side", "Ground floor"), ("Terrace", "First floor")]
# (number, section index, seats)
TABLES = [(n, 0, 4) for n in range(1, 9)] + [(n, 0, 2) for n in range(9, 13)] \
    + [(n, 1, 2) for n in range(13, 17)] + [(17, 1, 6), (18, 2, 4), (19, 2, 4), (20, 2, 8)]
CLEAN = dict(customers_present=False, plates_present=False, glasses_present=False, leftovers_present=False,
             table_clean=True, chairs_arranged=True, ready_for_next=True)
LEFT = dict(customers_present=False, plates_present=True, glasses_present=True, leftovers_present=True,
            table_clean=False, chairs_arranged=False, ready_for_next=False)
NAMES = ["Sharma", "Patel", "Iyer", "Khan", "Fernandes", "Das", "Mehta", "Nair", "Bose", "Reddy", "Gupta", "Joshi"]


class Command(BaseCommand):
    help = "Create the demo restaurant, staff, tables, bookings, tasks and history (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete the demo restaurant's data first.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG=True (it is for local demos).")
        if not settings.DEMO_PASSWORD:
            raise CommandError("DEMO_PASSWORD is empty. Set it in .env first.")
        self.rng = random.Random(7)
        with transaction.atomic():
            if options["reset"]:
                self.reset()
            self.restaurant = self.seed_restaurant()
            self.users = self.seed_users()
            self.tables = self.seed_tables()
            if not Reservation.objects.filter(restaurant=self.restaurant).exists():
                self.seed_history()
                self.seed_today()
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(f"  Restaurant: {self.restaurant.name} ({len(self.tables)} tables)")
        self.stdout.write(f"  Users: {', '.join(u[0] for u in DEMO_USERS)} (password: DEMO_PASSWORD in .env)")
        self.stdout.write(f"  Reservations: {Reservation.objects.filter(restaurant=self.restaurant).count()}, "
                          f"tasks: {WaiterTask.objects.filter(restaurant=self.restaurant).count()}, "
                          f"inspections: {TableInspection.objects.filter(table__restaurant=self.restaurant).count()}")

    # --- steps -----------------------------------------------------------------

    def reset(self):
        restaurant = Restaurant.objects.filter(slug=SLUG).first()
        if restaurant:
            for photo_inspection in TableInspection.objects.filter(table__restaurant=restaurant):
                for photo in photo_inspection.photos.all():
                    photo.image.delete(save=False)
            Notification.objects.filter(restaurant=restaurant).delete()
            TableInspection.objects.filter(table__restaurant=restaurant).delete()
            WaiterTask.objects.filter(restaurant=restaurant).delete()
            Reservation.objects.filter(restaurant=restaurant).delete()
            RestaurantTable.objects.filter(restaurant=restaurant).delete()
            User.objects.filter(username__in=[u[0] for u in DEMO_USERS]).delete()
            restaurant.delete()
        self.stdout.write("Existing demo data deleted.")

    def seed_restaurant(self):
        restaurant, _ = Restaurant.objects.get_or_create(
            slug=SLUG, defaults={"name": "The Copper Fork", "address": "14 Lake Road"}
        )
        for order, (name, floor) in enumerate(SECTIONS, start=1):
            RestaurantSection.objects.get_or_create(restaurant=restaurant, name=name, defaults={"floor": floor, "sort_order": order})
        return restaurant

    def seed_users(self):
        users = {}
        for username, first, last, role in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username, defaults={"first_name": first, "last_name": last, "email": f"{username}@example.com"}
            )
            if created:
                user.set_password(settings.DEMO_PASSWORD)
                user.save()
            user.profile.role, user.profile.restaurant = role, self.restaurant
            user.profile.save()
            users[username] = user
        return users

    def seed_tables(self):
        sections = list(self.restaurant.sections.order_by("sort_order"))
        waiters = [self.users[u] for u in ("waiter_demo", "waiter2_demo", "waiter3_demo", "waiter4_demo")]
        result = {}
        for number, section_index, seats in TABLES:
            table, created = RestaurantTable.objects.get_or_create(
                restaurant=self.restaurant, number=number,
                defaults={"section": sections[section_index], "capacity": seats, "assigned_waiter": waiters[(number - 1) // 5]},
            )
            if created:
                TableStatusHistory.objects.create(table=table, to_status="READY", is_occupied=False, cleanliness=Cleanliness.CLEAN,
                                                  is_blocked=False, source=TableStatusHistory.Source.SETUP, note="Table created")
            result[number] = table
        return result

    def backdate(self, model, pk, **fields):
        """Rewrite timestamps the services set to now(), so history looks like it happened in the past."""
        model.objects.filter(pk=pk).update(**fields)

    def seed_history(self):
        """Seven days of seated bookings, each followed by a cleaning task and inspection."""
        manager, reception = self.users["manager_demo"], self.users["reception_demo"]
        waiters = [self.users[u] for u in ("waiter_demo", "waiter2_demo", "waiter3_demo", "waiter4_demo")]
        today = timezone.localdate()
        for day_offset in range(7, 0, -1):
            day = today - timedelta(days=day_offset)
            for _ in range(self.rng.randint(9, 15)):
                table = self.rng.choice(list(self.tables.values()))
                hour = self.rng.choice([12, 13, 13, 14, 19, 19, 20, 20, 21])
                start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time())) + timedelta(hours=hour, minutes=self.rng.choice([0, 15, 30, 45]))
                end = start + timedelta(minutes=90)
                if bookings.conflicts(table, start, end).exists():
                    continue
                party = self.rng.randint(1, table.capacity)
                waiter = table.assigned_waiter or waiters[0]
                arrived = start + timedelta(minutes=self.rng.randint(-5, 10))
                seated = arrived + timedelta(minutes=self.rng.randint(0, 12))
                left = seated + timedelta(minutes=self.rng.randint(45, 100))
                reservation = Reservation.objects.create(
                    restaurant=self.restaurant, table=table, customer_name=self.rng.choice(NAMES), phone="98765 00000",
                    party_size=party, start_at=start, end_at=end, status=Reservation.Status.COMPLETED,
                    created_by=reception, created_at=start - timedelta(days=1), confirmed_at=start - timedelta(days=1),
                    arrived_at=arrived, seated_at=seated, completed_at=left,
                )
                reservation.history.create(to_status="CONFIRMED", changed_by=reception, created_at=reservation.created_at)
                reservation.history.create(from_status="CONFIRMED", to_status="SEATED", changed_by=reception, created_at=seated)
                reservation.history.create(from_status="SEATED", to_status="COMPLETED", changed_by=reception, created_at=left)
                # Audit rows so occupancy can be rebuilt for the reports.
                TableStatusHistory.objects.create(table=table, from_status="READY", to_status="OCCUPIED", is_occupied=True,
                                                  cleanliness=Cleanliness.CLEAN, is_blocked=False,
                                                  source=TableStatusHistory.Source.RESERVATION, changed_by=reception,
                                                  note=f"{reservation.reference} seated", created_at=seated)
                TableStatusHistory.objects.create(table=table, from_status="OCCUPIED", to_status="VACANT_NEEDS_CLEARING",
                                                  is_occupied=False, cleanliness=Cleanliness.NEEDS_CLEARING, is_blocked=False,
                                                  source=TableStatusHistory.Source.INSPECTION, changed_by=waiter,
                                                  note="Guests left", created_at=left)
                TableInspection.objects.create(table=table, inspector=waiter, created_at=left, resulting_status="VACANT_NEEDS_CLEARING", **LEFT)
                assigned = left + timedelta(minutes=1)
                started = assigned + timedelta(minutes=self.rng.randint(1, 8))
                done = started + timedelta(minutes=self.rng.randint(4, 14))
                task = WaiterTask.objects.create(
                    restaurant=self.restaurant, table=table, task_type=WaiterTask.TaskType.FULL_RESET,
                    priority=self.rng.choice(["NORMAL", "NORMAL", "HIGH"]), status=WaiterTask.Status.COMPLETED,
                    assigned_to=waiter, created_by=manager, created_at=assigned, assigned_at=assigned,
                    accepted_at=started, started_at=started, completed_at=done, due_at=assigned + timedelta(minutes=15),
                    completion_remarks="Reset for the next guests",
                )
                task.history.create(action="CREATED", to_status="OPEN", changed_by=manager, created_at=assigned)
                task.history.create(action="ASSIGNED", from_status="OPEN", to_status="ASSIGNED", changed_by=manager, created_at=assigned)
                task.history.create(action="STARTED", from_status="ASSIGNED", to_status="IN_PROGRESS", changed_by=waiter, created_at=started)
                task.history.create(action="COMPLETED", from_status="IN_PROGRESS", to_status="COMPLETED", changed_by=waiter, created_at=done)
                TableInspection.objects.create(table=table, inspector=waiter, task=task, source=TableInspection.Source.TASK,
                                               created_at=done, resulting_status="READY", **CLEAN)
                TableStatusHistory.objects.create(table=table, from_status="CLEANING_IN_PROGRESS", to_status="READY", is_occupied=False,
                                                  cleanliness=Cleanliness.CLEAN, is_blocked=False, source=TableStatusHistory.Source.TASK,
                                                  changed_by=waiter, note=f"{task.task_code}", created_at=done)
        # Tables were created "a week ago".
        RestaurantTable.objects.filter(restaurant=self.restaurant).update(status_updated_at=timezone.now() - timedelta(hours=2))

    def seed_today(self):
        """A live floor for the demo: occupied, dirty, cleaning, blocked and booked tables."""
        manager, reception = self.users["manager_demo"], self.users["reception_demo"]
        now = timezone.now()
        t = self.tables

        # Seated guests at T1, T3, T14 (walk-ins and a booking that arrived).
        for number, party in ((1, 4), (3, 2), (14, 2)):
            bookings.seat_walk_in(restaurant=self.restaurant, table=t[number], party_size=party,
                                  customer_name=self.rng.choice(NAMES), created_by=reception)
        # T12: guests just left - dishes on the table (the demo's "Table 12" story starts here).
        inspections.record_inspection(t[12], t[12].assigned_waiter, remarks="Guests left, two plates and glasses", **LEFT)
        # T5: cleaning required (dishes cleared by a camera detection, not yet cleaned).
        inspections.record_inspection(t[5], t[5].assigned_waiter, **LEFT)
        inspections.record_detection(t[5], customers_present=False, plates_present=False, glasses_present=False,
                                     leftovers_present=False, confidence=0.91, device_id="cam-hall-2")
        # T7: cleaning in progress by waiter_demo.
        inspections.record_inspection(t[7], t[7].assigned_waiter, **LEFT)
        task = tasks.create_task(table=t[7], task_type=WaiterTask.TaskType.FULL_RESET, priority="HIGH",
                                 created_by=manager, assignee=t[7].assigned_waiter)
        tasks.start(task, user=t[7].assigned_waiter)
        # T20: blocked.
        tables.set_blocked(t[20], blocked=True, reason="Private party set-up until 18:00", user=manager)
        # Bookings: one in the hold window (T17 reserved), others later today and tomorrow.
        local_now = timezone.localtime(now)
        bookings.create_reservation(restaurant=self.restaurant, table=t[17], customer_name="Kapoor", phone="98200 11223",
                                    party_size=5, start_at=now + timedelta(minutes=20), created_by=reception, remarks="Birthday")
        for number, hours, party, name in ((2, 3, 4, "Verma"), (18, 4, 3, "D'Souza"), (9, 5, 2, "Rao")):
            bookings.create_reservation(restaurant=self.restaurant, table=t[number], customer_name=name, phone="98100 55667",
                                        party_size=party, start_at=now + timedelta(hours=hours), created_by=reception)
        tomorrow_7pm = timezone.make_aware(timezone.datetime.combine(local_now.date() + timedelta(days=1), timezone.datetime.min.time())) + timedelta(hours=19)
        bookings.create_reservation(restaurant=self.restaurant, table=t[8], customer_name="Menon", phone="98300 99887",
                                    party_size=4, start_at=tomorrow_7pm, created_by=reception)
        # Notifications created while seeding are noise for the demo accounts.
        Notification.objects.filter(restaurant=self.restaurant).delete()
