"""Business-rule tests for table state, inspections, tasks and reservations (no pages involved)."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.testutils import (CLEAN_CHECK, GUESTS_LEFT_CHECK, at, make_manager, make_receptionist, make_restaurant,
                            make_table, make_waiter)
from inspections import services as inspections
from inspections.models import TableInspection
from notifications.models import Notification
from reservations import services as bookings
from reservations.models import Reservation
from tasks import services as tasks
from tasks.models import WaiterTask

from . import services
from .models import Cleanliness, TableStatus, TableStatusHistory, compute_status

S = TableStatus


class StatusRuleTests(TestCase):
    def rule(self, **overrides):
        facts = dict(is_active=True, is_blocked=False, is_occupied=False, cleanliness=Cleanliness.CLEAN,
                     cleaning_in_progress=False, reservation_hold=False)
        facts.update(overrides)
        return compute_status(**facts)

    def test_priority_order(self):
        self.assertEqual(self.rule(), S.READY)
        self.assertEqual(self.rule(reservation_hold=True), S.RESERVED)
        self.assertEqual(self.rule(cleanliness=Cleanliness.NEEDS_CLEANING, reservation_hold=True), S.CLEANING_REQUIRED)
        self.assertEqual(self.rule(cleanliness=Cleanliness.NEEDS_CLEARING), S.VACANT_NEEDS_CLEARING)
        self.assertEqual(self.rule(cleanliness=Cleanliness.NEEDS_CLEARING, cleaning_in_progress=True), S.CLEANING_IN_PROGRESS)
        self.assertEqual(self.rule(is_occupied=True, cleanliness=Cleanliness.NEEDS_CLEARING), S.OCCUPIED)
        self.assertEqual(self.rule(is_occupied=True, is_blocked=True), S.BLOCKED)
        self.assertEqual(self.rule(is_blocked=True, is_active=False), S.INACTIVE)

    def test_vacant_table_with_dishes_is_never_ready(self):
        self.assertNotEqual(self.rule(cleanliness=Cleanliness.NEEDS_CLEARING), S.READY)


class BaseCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = make_restaurant()
        cls.manager = make_manager(cls.restaurant)
        cls.waiter = make_waiter(cls.restaurant)
        cls.waiter2 = make_waiter(cls.restaurant, "waiter2_t")
        cls.receptionist = make_receptionist(cls.restaurant)

    def setUp(self):
        self.table = make_table(self.restaurant, 12, capacity=4)

    def status(self, table=None):
        table = table or self.table
        table.refresh_from_db()
        return services.status_of(table)


class InspectionTests(BaseCase):
    def test_guests_left_makes_table_vacant_needs_clearing(self):
        self.table.is_occupied = True
        self.table.save()
        inspection = inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        self.assertEqual(self.status(), S.VACANT_NEEDS_CLEARING)
        self.assertEqual(inspection.resulting_status, S.VACANT_NEEDS_CLEARING)
        self.assertIsNotNone(self.table.last_inspected_at)
        row = TableStatusHistory.objects.filter(table=self.table).first()
        self.assertEqual((row.from_status, row.to_status), (S.OCCUPIED, S.VACANT_NEEDS_CLEARING))

    def test_clean_but_not_confirmed_ready_needs_cleaning(self):
        inspections.record_inspection(self.table, self.waiter, **dict(CLEAN_CHECK, ready_for_next=False))
        self.assertEqual(self.status(), S.CLEANING_REQUIRED)

    def test_contradictory_ready_answer_rejected(self):
        with self.assertRaises(ValidationError):
            inspections.record_inspection(self.table, self.waiter, **dict(CLEAN_CHECK, plates_present=True))

    def test_waiter_cannot_inspect_another_waiters_table(self):
        self.table.assigned_waiter = self.waiter2
        self.table.save()
        with self.assertRaises(PermissionDenied):
            inspections.record_inspection(self.table, self.waiter, **CLEAN_CHECK)

    def test_receptionist_cannot_inspect(self):
        with self.assertRaises(PermissionDenied):
            inspections.record_inspection(self.table, self.receptionist, **CLEAN_CHECK)

    def test_managers_notified(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        kinds = set(Notification.objects.filter(recipient=self.manager).values_list("kind", flat=True))
        self.assertIn(Notification.Kind.INSPECTION, kinds)
        self.assertIn(Notification.Kind.CLEANING_NEEDED, kinds)

    def test_camera_reports_dishes_but_never_makes_table_ready(self):
        inspections.record_detection(self.table, customers_present=False, dishes_present=True,
                                     confidence=0.9, device_id="cam-1")
        self.assertEqual(self.status(), S.VACANT_NEEDS_CLEARING)
        inspections.record_detection(self.table, customers_present=False, dishes_present=False,
                                     confidence=0.9, device_id="cam-1")
        self.assertEqual(self.status(), S.CLEANING_REQUIRED)  # dishes gone, still not Ready

    def test_low_confidence_detection_is_stored_not_applied(self):
        detection = inspections.record_detection(self.table, customers_present=True, dishes_present=True, confidence=0.2)
        self.assertFalse(detection.applied)
        self.assertEqual(self.status(), S.READY)


class TaskWorkflowTests(BaseCase):
    def dirty_table(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)

    def new_task(self, assignee=None):
        return tasks.create_task(table=self.table, task_type=WaiterTask.TaskType.FULL_RESET,
                                 priority=WaiterTask.Priority.HIGH, created_by=self.manager, assignee=assignee)

    def test_full_cycle_makes_table_ready_only_after_clean_check(self):
        self.dirty_table()
        task = self.new_task(assignee=self.waiter)
        self.assertEqual(task.status, WaiterTask.Status.ASSIGNED)
        self.assertTrue(Notification.objects.filter(recipient=self.waiter, kind=Notification.Kind.TASK_ASSIGNED).exists())
        tasks.accept(task, user=self.waiter)
        tasks.start(task, user=self.waiter)
        self.assertEqual(self.status(), S.CLEANING_IN_PROGRESS)
        tasks.complete(task, user=self.waiter, check=CLEAN_CHECK, remarks="Wiped and reset")
        self.assertEqual(self.status(), S.READY)
        row = TableStatusHistory.objects.filter(table=self.table).first()
        self.assertEqual((row.from_status, row.to_status), (S.CLEANING_IN_PROGRESS, S.READY))
        self.assertEqual(TableInspection.objects.filter(task=task).count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind=Notification.Kind.TASK_COMPLETED).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.receptionist, kind=Notification.Kind.TABLE_READY).exists())

    def test_completing_with_dishes_left_does_not_make_table_ready(self):
        self.dirty_table()
        task = self.new_task(assignee=self.waiter)
        tasks.start(task, user=self.waiter)
        tasks.complete(task, user=self.waiter, check=dict(CLEAN_CHECK, glasses_present=True, ready_for_next=False))
        self.assertEqual(self.status(), S.VACANT_NEEDS_CLEARING)

    def test_other_waiter_cannot_start_or_complete(self):
        task = self.new_task(assignee=self.waiter)
        with self.assertRaises(PermissionDenied):
            tasks.start(task, user=self.waiter2)
        tasks.start(task, user=self.waiter)
        with self.assertRaises(PermissionDenied):
            tasks.complete(task, user=self.waiter2, check=CLEAN_CHECK)

    def test_only_managers_create_and_assign(self):
        with self.assertRaises(PermissionDenied):
            tasks.create_task(table=self.table, task_type="CLEAN_TABLE", priority="NORMAL", created_by=self.waiter)
        task = self.new_task()
        with self.assertRaises(PermissionDenied):
            tasks.assign(task, self.waiter, user=self.waiter)

    def test_invalid_transitions(self):
        task = self.new_task()
        with self.assertRaises(ValidationError):
            tasks.start(task, user=self.manager)  # open, not assigned
        task = tasks.assign(task, self.waiter, user=self.manager)
        with self.assertRaises(ValidationError):
            tasks.complete(task, user=self.waiter, check=CLEAN_CHECK)  # not started

    def test_reassign_and_priority_notify_waiters(self):
        task = self.new_task(assignee=self.waiter)
        tasks.start(task, user=self.waiter)
        task = tasks.reassign(task, self.waiter2, user=self.manager)
        self.assertEqual((task.status, task.assigned_to), (WaiterTask.Status.ASSIGNED, self.waiter2))
        tasks.change_priority(task, WaiterTask.Priority.URGENT, user=self.manager)
        kinds = list(Notification.objects.filter(recipient=self.waiter2).values_list("kind", flat=True))
        self.assertIn(Notification.Kind.TASK_REASSIGNED, kinds)
        self.assertIn(Notification.Kind.TASK_PRIORITY, kinds)

    def test_overdue_tasks_notified_once(self):
        task = self.new_task(assignee=self.waiter)
        WaiterTask.objects.filter(pk=task.pk).update(due_at=at(minutes=-5))
        self.assertEqual(tasks.notify_overdue_tasks(self.restaurant), 1)
        self.assertEqual(tasks.notify_overdue_tasks(self.restaurant), 0)
        self.assertTrue(Notification.objects.filter(recipient=self.manager, kind=Notification.Kind.TASK_OVERDUE).exists())


class ReservationTests(BaseCase):
    def book(self, start, hours=None, party=2, table=None, user=None):
        return bookings.create_reservation(
            restaurant=self.restaurant, table=table or self.table, customer_name="Mehta", phone="98765 43210",
            party_size=party, start_at=start, created_by=user or self.receptionist,
            duration_minutes=int(hours * 60) if hours else None,
        )

    def test_double_booking_is_prevented(self):
        self.book(at(hours=2))
        with self.assertRaises(ValidationError):
            self.book(at(hours=2, minutes=30))  # overlaps the 90-minute slot
        self.book(at(hours=3, minutes=30))  # starts exactly when the first ends: allowed
        self.assertEqual(Reservation.objects.count(), 2)

    def test_future_booking_does_not_block_table_now(self):
        self.book(at(hours=5))
        self.assertEqual(self.status(), S.READY)

    def test_table_shows_reserved_during_hold_window(self):
        self.book(at(minutes=20))
        self.assertEqual(self.status(), S.RESERVED)

    def test_cancellation_frees_the_slot_and_the_table(self):
        booking = self.book(at(minutes=20))
        bookings.change_status(booking, Reservation.Status.CANCELLED, self.receptionist)
        self.assertEqual(self.status(), S.READY)
        self.book(at(minutes=20))  # slot can be booked again

    def test_capacity_and_role_checks(self):
        with self.assertRaises(ValidationError):
            self.book(at(hours=2), party=6)
        with self.assertRaises(PermissionDenied):
            self.book(at(hours=2), user=self.waiter)

    def test_seating_needs_a_clean_table_and_completion_needs_clearing(self):
        booking = self.book(at(minutes=10))
        self.table.cleanliness = Cleanliness.NEEDS_CLEARING
        self.table.save()
        with self.assertRaises(ValidationError):
            bookings.change_status(booking, Reservation.Status.SEATED, self.receptionist)
        services.change_table_state(self.table, source=TableStatusHistory.Source.OVERRIDE, cleanliness=Cleanliness.CLEAN)
        bookings.change_status(booking, Reservation.Status.ARRIVED, self.receptionist)
        bookings.change_status(booking, Reservation.Status.SEATED, self.receptionist)
        self.assertEqual(self.status(), S.OCCUPIED)
        bookings.change_status(booking, Reservation.Status.COMPLETED, self.receptionist)
        self.assertEqual(self.status(), S.VACANT_NEEDS_CLEARING)

    def test_inspection_after_guests_left_completes_the_seated_booking(self):
        booking = bookings.seat_walk_in(restaurant=self.restaurant, table=self.table, party_size=2,
                                        created_by=self.receptionist)
        self.assertEqual(booking.status, Reservation.Status.SEATED)
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Reservation.Status.COMPLETED)

    def test_no_show_only_after_start(self):
        booking = self.book(at(minutes=30))
        with self.assertRaises(ValidationError):
            bookings.change_status(booking, Reservation.Status.NO_SHOW, self.receptionist)

    def test_available_tables_respects_time_capacity_and_blocks(self):
        big = make_table(self.restaurant, 20, capacity=8)
        blocked = make_table(self.restaurant, 21, capacity=4, is_blocked=True)
        self.book(at(hours=2))
        found = bookings.available_tables(self.restaurant, start=at(hours=2), end=at(hours=3), party_size=3)
        numbers = [t.number for t in found]
        self.assertNotIn(12, numbers)  # booked
        self.assertNotIn(blocked.number, numbers)
        self.assertEqual(numbers, [big.number])
        later = bookings.available_tables(self.restaurant, start=at(hours=6), end=at(hours=7), party_size=3)
        self.assertEqual([t.number for t in later], [12, 20])  # smallest suitable first

    def test_database_rejects_inverted_time_slot(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Reservation.objects.create(restaurant=self.restaurant, table=self.table, customer_name="x", party_size=2,
                                       start_at=at(hours=2), end_at=at(hours=1), created_by=self.receptionist)
