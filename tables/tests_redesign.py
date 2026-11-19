"""The simplified UI: four display states, the one-tap check, the waiter's Done button and the manager's Remind."""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core.testutils import GUESTS_LEFT_CHECK, make_manager, make_receptionist, make_restaurant, make_table, make_waiter
from inspections import services as inspections
from notifications.models import Notification
from tables import services
from tables.models import Cleanliness, DisplayState, RestaurantTable, TableStatus, TableStatusHistory
from tasks import services as tasks
from tasks.models import WaiterTask


def live(table):
    return services.with_status(services.live_tables(table.restaurant).filter(pk=table.pk))[0]


class RedesignCase(TestCase):
    def setUp(self):
        cache.clear()
        self.restaurant = make_restaurant()
        self.manager = make_manager(self.restaurant)
        self.waiter = make_waiter(self.restaurant)
        self.receptionist = make_receptionist(self.restaurant)
        self.table = make_table(self.restaurant, 5, assigned_waiter=self.waiter)


class DisplayStateTests(RedesignCase):
    def test_every_cleaning_stage_is_needs_cleaning(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        self.assertEqual(live(self.table).state, DisplayState.DIRTY)
        task = tasks.create_task(table=self.table, task_type="FULL_RESET", priority="NORMAL",
                                 created_by=self.manager, assignee=self.waiter)
        tasks.start(task, user=self.waiter)
        table = live(self.table)
        self.assertEqual(table.state, DisplayState.DIRTY)
        self.assertTrue(table.cleaning_now)

    def test_reserved_and_blocked_are_unavailable(self):
        services.set_blocked(self.table, blocked=True, reason="Wobbly leg", user=self.manager)
        self.assertEqual(live(self.table).state, DisplayState.UNAVAILABLE)

    def test_seating_does_not_start_the_dirty_timer(self):
        services.change_table_state(self.table, source=TableStatusHistory.Source.OVERRIDE, is_occupied=True,
                                    cleanliness=Cleanliness.NEEDS_CLEARING)
        self.table.refresh_from_db()
        self.assertIsNone(self.table.dirty_since)
        services.change_table_state(self.table, source=TableStatusHistory.Source.OVERRIDE, is_occupied=False)
        self.table.refresh_from_db()
        self.assertIsNotNone(self.table.dirty_since)

    def test_old_filter_links_still_work(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("tables:floor") + "?status=available")
        self.assertContains(page, 'data-state="free"')


class QuickCheckTests(RedesignCase):
    def tap(self, choice):
        return self.client.post(reverse("inspections:inspect", args=[self.table.pk]), {"quick": choice})

    def test_three_taps_drive_the_table(self):
        self.client.force_login(self.waiter)
        self.assertRedirects(self.tap("seated"), reverse("tasks:waiter_home"))
        self.assertEqual(live(self.table).status, TableStatus.OCCUPIED)
        self.tap("dirty")
        self.assertEqual(live(self.table).state, DisplayState.DIRTY)
        self.tap("ready")
        self.assertEqual(live(self.table).status, TableStatus.READY)

    def test_check_page_offers_the_three_buttons(self):
        self.client.force_login(self.waiter)
        page = self.client.get(reverse("inspections:inspect", args=[self.table.pk]))
        for value in ("seated", "dirty", "ready"):
            self.assertContains(page, f'name="quick" value="{value}"')

    def test_waiter_cannot_quick_check_another_waiters_table(self):
        other = make_table(self.restaurant, 6, assigned_waiter=make_waiter(self.restaurant, "waiter2_t"))
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("inspections:inspect", args=[other.pk]), {"quick": "ready"})
        self.assertEqual(response.status_code, 403)


class WaiterDoneAndRemindTests(RedesignCase):
    def make_task(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        return tasks.create_task(table=self.table, task_type="FULL_RESET", priority="NORMAL",
                                 created_by=self.manager, assignee=self.waiter)

    def test_done_starts_and_completes_in_one_tap(self):
        task = self.make_task()
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("tasks:done", args=[task.pk]))
        self.assertRedirects(response, reverse("tasks:waiter_home"))
        task.refresh_from_db()
        self.assertEqual(task.status, WaiterTask.Status.COMPLETED)
        self.assertEqual(live(self.table).status, TableStatus.READY)

    def test_another_waiter_cannot_press_done(self):
        task = self.make_task()
        self.client.force_login(make_waiter(self.restaurant, "waiter2_t"))
        self.assertEqual(self.client.post(reverse("tasks:done", args=[task.pk])).status_code, 404)

    def test_manager_reminds_the_waiter(self):
        task = self.make_task()
        self.client.force_login(self.manager)
        self.assertRedirects(self.client.post(reverse("tasks:remind", args=[task.pk])), reverse("core:manager_dashboard"))
        self.assertTrue(Notification.objects.filter(recipient=self.waiter, title__startswith="Reminder: T5").exists())
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.post(reverse("tasks:remind", args=[task.pk])).status_code, 403)


class FloorScreenTests(RedesignCase):
    def test_manager_and_receptionist_share_one_floor(self):
        for user, url in ((self.manager, "core:manager_dashboard"), (self.receptionist, "reservations:desk")):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                page = self.client.get(reverse(url))
                self.assertContains(page, 'id="floorGrid"')
                self.assertContains(page, 'id="bookingDrawer"')
        self.assertContains(page, "Arriving soon")  # receptionist's to-do
        self.assertNotContains(page, "Needs you now")

    def test_walk_in_returns_to_the_floor_it_came_from(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("reservations:walk_in"), {
            "walkin-table": self.table.pk, "walkin-guests": 2, "next": reverse("core:manager_dashboard"),
        })
        self.assertRedirects(response, reverse("core:manager_dashboard"))
        self.assertTrue(RestaurantTable.objects.get(pk=self.table.pk).is_occupied)

    def test_unsafe_next_is_ignored(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("reservations:walk_in"), {
            "walkin-table": self.table.pk, "walkin-guests": 2, "next": "//evil.example/",
        })
        self.assertRedirects(response, reverse("reservations:desk"), fetch_redirect_response=False)
