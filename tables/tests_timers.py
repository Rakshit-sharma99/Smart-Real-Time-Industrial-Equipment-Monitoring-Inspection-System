"""Seated / dirty timers, stale tables, the attention order, dated deadlines and the camera status labels."""
from datetime import date, time, timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cctv.models import Camera
from core import services as overview
from core.templatetags.ui import due_label, format_minutes
from core.testutils import make_manager, make_restaurant, make_table, make_waiter
from reports import analytics
from tables import services
from tables.models import Cleanliness, RestaurantTable, TableStatus, TableStatusHistory
from tasks.models import WaiterTask

Source = TableStatusHistory.Source


def live(table):
    return services.with_status(services.live_tables(table.restaurant).filter(pk=table.pk))[0]


def backdate(table, minutes, **fields):
    """Pretend the current sitting / mess started `minutes` ago."""
    when = timezone.now() - timedelta(minutes=minutes)
    RestaurantTable.objects.filter(pk=table.pk).update(**{name: when for name in fields})


class TimerTests(TestCase):
    def setUp(self):
        cache.clear()
        self.restaurant = make_restaurant()
        self.waiter = make_waiter(self.restaurant)
        self.table = make_table(self.restaurant, 3, assigned_waiter=self.waiter)

    def test_seating_starts_and_leaving_stops_the_seated_timer(self):
        services.change_table_state(self.table, source=Source.OVERRIDE, is_occupied=True)
        self.table.refresh_from_db()
        self.assertIsNotNone(self.table.occupied_since)
        self.assertEqual(live(self.table).timer["kind"], "seated")

        services.change_table_state(self.table, source=Source.OVERRIDE, is_occupied=False,
                                    cleanliness=Cleanliness.NEEDS_CLEARING)
        self.table.refresh_from_db()
        self.assertIsNone(self.table.occupied_since)
        self.assertIsNotNone(self.table.dirty_since)
        self.assertEqual(live(self.table).timer["kind"], "dirty")

    def test_dirty_timer_keeps_its_start_while_the_mess_changes(self):
        services.change_table_state(self.table, source=Source.OVERRIDE, cleanliness=Cleanliness.NEEDS_CLEARING)
        backdate(self.table, 10, dirty_since=True)
        services.change_table_state(self.table, source=Source.OVERRIDE, cleanliness=Cleanliness.NEEDS_CLEANING)
        self.assertGreaterEqual(live(self.table).timer["minutes"], 10)
        services.change_table_state(self.table, source=Source.OVERRIDE, cleanliness=Cleanliness.CLEAN)
        self.assertIsNone(live(self.table).timer)

    def test_levels_follow_the_sitting_length(self):
        services.change_table_state(self.table, source=Source.OVERRIDE, is_occupied=True)
        limit = self.restaurant.reservation_minutes
        for minutes, level in ((10, "ok"), (int(limit * 0.8), "warn"), (limit + 5, "late"),
                               (self.restaurant.stale_after_minutes + 1, "stale")):
            with self.subTest(minutes=minutes):
                backdate(self.table, minutes, occupied_since=True)
                self.assertEqual(live(self.table).timer["level"], level)

    def test_an_inspection_confirms_a_long_sitting(self):
        services.change_table_state(self.table, source=Source.OVERRIDE, is_occupied=True)
        backdate(self.table, self.restaurant.stale_after_minutes + 60, occupied_since=True)
        self.assertTrue(live(self.table).is_stale)
        RestaurantTable.objects.filter(pk=self.table.pk).update(last_inspected_at=timezone.now())
        table = live(self.table)
        self.assertFalse(table.is_stale)
        self.assertEqual(table.timer["level"], "late")


class AttentionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.restaurant = make_restaurant()
        self.waiter = make_waiter(self.restaurant)

    def test_worst_first_and_unassigned_tables_flagged(self):
        stale = make_table(self.restaurant, 14, assigned_waiter=self.waiter)
        services.change_table_state(stale, source=Source.OVERRIDE, is_occupied=True)
        backdate(stale, 8 * 24 * 60, occupied_since=True)
        dirty = make_table(self.restaurant, 7, assigned_waiter=self.waiter)
        services.change_table_state(dirty, source=Source.OVERRIDE, cleanliness=Cleanliness.NEEDS_CLEARING)
        make_table(self.restaurant, 21)  # no waiter

        tables = services.with_status(services.live_tables(self.restaurant))
        items = overview.attention_items(self.restaurant, tables)
        self.assertEqual([i["table"].label for i in items], ["T14", "T7", "T21"])
        self.assertIn("please verify", items[0]["title"])
        self.assertEqual(items[-1]["title"], "has no waiter")

    def test_blocked_tables_are_counted(self):
        table = make_table(self.restaurant, 20, assigned_waiter=self.waiter)
        make_table(self.restaurant, 1, assigned_waiter=self.waiter)
        services.set_blocked(table, blocked=True, reason="Broken leg", user=None)
        stats = overview.overview_stats(services.with_status(services.live_tables(self.restaurant)))
        self.assertEqual(stats["unavailable"], 1)
        self.assertEqual(stats["free"] + stats["seated"] + stats["dirty"] + stats["unavailable"], stats["total"])


class ReportOccupancyTests(TestCase):
    def test_occupancy_only_counts_opening_hours_and_caps_unconfirmed_sittings(self):
        restaurant = make_restaurant()
        table = make_table(restaurant, 1)
        day = timezone.localdate() - timedelta(days=3)
        seated = timezone.make_aware(timezone.datetime.combine(day, time(22, 0)))
        TableStatusHistory.objects.create(table=table, to_status=TableStatus.OCCUPIED, is_occupied=True,
                                          cleanliness=Cleanliness.CLEAN, is_blocked=False, source=Source.OVERRIDE,
                                          created_at=seated)
        report = analytics.operations_report(restaurant, day, day + timedelta(days=1))
        hours = [hour for hour, _ in report.peak_hours]
        self.assertEqual(hours, list(range(11, 23)))
        self.assertEqual(report.capped_sittings, 1)
        # 22:00-23:00 on the first day only: the night is closed and the sitting is capped at 4 h.
        self.assertEqual(dict(report.peak_hours)[22], 0.5)
        self.assertEqual(sum(value for _, value in report.peak_hours), 0.5)


class LabelTests(TestCase):
    def test_minutes(self):
        self.assertEqual(format_minutes(42), "42 min")
        self.assertEqual(format_minutes(125), "2 h 5 min")
        self.assertEqual(format_minutes(8 * 24 * 60 + 5), "8 days")

    def test_due_label_names_the_day(self):
        task = WaiterTask(due_at=timezone.now() - timedelta(days=8), status=WaiterTask.Status.ASSIGNED)
        self.assertEqual(due_label(task), "Overdue by 8 days")
        task.due_at = timezone.now() + timedelta(days=2)
        self.assertRegex(due_label(task), r"^Due \w{3} \d{1,2} \w{3}, \d{1,2}:\d{2} [AP]M$")


class CameraStatusPageTests(TestCase):
    def test_offline_camera_says_so(self):
        restaurant = make_restaurant()
        manager = make_manager(restaurant)
        Camera.objects.create(restaurant=restaurant, name="Hall", source="rtsp://cam/1", status=Camera.Status.ONLINE,
                              last_frame_at=timezone.now() - timedelta(days=8))
        self.client.force_login(manager)
        page = self.client.get(reverse("cctv:console")).content.decode()
        self.assertIn(">OFFLINE<", page)
        self.assertNotIn(">LIVE<", page)
        self.assertIn("Offline for 8 days", page)
        self.assertIn("Camera offline", page)  # top bar
