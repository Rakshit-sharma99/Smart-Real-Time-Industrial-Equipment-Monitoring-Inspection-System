"""Page, permission, API and WebSocket tests (business rules live in tables/tests_state.py)."""
import json
from io import StringIO

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.consumers import LiveConsumer
from core.testutils import (CLEAN_CHECK, GUESTS_LEFT_CHECK, TEST_PASSWORD, at, make_manager, make_receptionist,
                            make_restaurant, make_table, make_waiter)
from inspections import services as inspections
from inspections.models import TableInspection
from reservations import services as bookings
from reservations.models import Reservation
from tables import services as table_services
from tables.models import RestaurantTable, TableStatus
from tasks import services as tasks
from tasks.models import WaiterTask


class StaffCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = make_restaurant()
        cls.other_restaurant = make_restaurant("Other Place", "other")
        cls.manager = make_manager(cls.restaurant)
        cls.waiter = make_waiter(cls.restaurant)
        cls.waiter2 = make_waiter(cls.restaurant, "waiter2_t")
        cls.receptionist = make_receptionist(cls.restaurant)
        cls.outsider = make_manager(cls.other_restaurant, "other_manager")
        cls.table = make_table(cls.restaurant, 12, capacity=4, assigned_waiter=cls.waiter)
        cls.other_table = make_table(cls.other_restaurant, 1)

    def setUp(self):
        cache.clear()


class RoleMatrixTests(StaffCase):
    """Table-driven: who may open which page."""

    def manager_only(self):
        return [
            reverse("core:manager_dashboard"), reverse("tables:manage"), reverse("tables:create"),
            reverse("tables:edit", args=[self.table.pk]), reverse("tables:qr_sheet"), reverse("tasks:list"),
            reverse("tasks:create"), reverse("accounts:staff_list"), reverse("reports:home"),
            reverse("core:restaurant_settings"), reverse("core:sections"),
        ]

    def test_manager_opens_manager_pages(self):
        self.client.force_login(self.manager)
        for url in self.manager_only():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_waiter_and_receptionist_get_403_on_manager_pages(self):
        for user in (self.waiter, self.receptionist):
            self.client.force_login(user)
            for url in self.manager_only():
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_receptionist_cannot_inspect_or_see_tasks(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.get(reverse("inspections:inspect", args=[self.table.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("inspections:scanner")).status_code, 403)
        self.assertEqual(self.client.get(reverse("tasks:waiter_home")).status_code, 403)

    def test_waiter_cannot_use_booking_desk(self):
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("reservations:desk")).status_code, 403)
        self.assertEqual(self.client.post(reverse("reservations:book"), {}).status_code, 403)

    def test_each_role_lands_on_its_dashboard(self):
        for user, target in ((self.manager, "core:manager_dashboard"), (self.waiter, "tasks:waiter_home"),
                             (self.receptionist, "reservations:desk")):
            self.client.force_login(user)
            self.assertRedirects(self.client.get("/"), reverse(target))

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("tables:floor"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_other_restaurant_is_invisible(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("tables:detail", args=[self.table.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("tables:floor")), "T12")
        self.assertEqual(self.client.get(f"/t/{self.table.public_id}/").status_code, 404)

    def test_sidebar_differs_per_role(self):
        self.client.force_login(self.waiter)
        page = self.client.get(reverse("tasks:waiter_home"))
        self.assertContains(page, "My tasks")
        self.assertNotContains(page, reverse("reports:home"))
        self.client.force_login(self.receptionist)
        page = self.client.get(reverse("reservations:desk"))
        self.assertNotContains(page, reverse("inspections:scanner"))


class LoginTests(StaffCase):
    def test_login_and_logout(self):
        response = self.client.post(reverse("accounts:login"), {"username": "waiter_t", "password": TEST_PASSWORD})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_after_failed_attempts(self):
        for _ in range(5):
            self.client.post(reverse("accounts:login"), {"username": "waiter_t", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "waiter_t", "password": TEST_PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_user_blocked(self):
        self.waiter.is_active = False
        self.waiter.save()
        self.client.post(reverse("accounts:login"), {"username": "waiter_t", "password": TEST_PASSWORD})
        self.assertNotIn("_auth_user_id", self.client.session)


class TableWorkflowPageTests(StaffCase):
    """The Table 12 story through the real pages."""

    def answers(self, check, **extra):
        data = {name: "1" if value else "0" for name, value in check.items()}
        data.update(extra)
        return data

    def test_manager_creates_table(self):
        self.client.force_login(self.manager)
        section = self.restaurant.sections.first()
        response = self.client.post(reverse("tables:create"), {"number": 30, "section": section.pk, "capacity": 6, "assigned_waiter": ""})
        table = RestaurantTable.objects.get(restaurant=self.restaurant, number=30)
        self.assertRedirects(response, table.get_absolute_url())
        response = self.client.post(reverse("tables:create"), {"number": 30, "section": section.pk, "capacity": 6})
        self.assertContains(response, "already exists")

    def test_full_story(self):
        # 1. The assigned waiter inspects T12 after the guests leave.
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("inspections:inspect", args=[self.table.pk]), self.answers(GUESTS_LEFT_CHECK, remarks="Dishes left"))
        self.assertRedirects(response, self.table.get_absolute_url())
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.VACANT_NEEDS_CLEARING)

        # 2. The manager sees it and assigns a cleaning task.
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("core:fragment_kpis")), 'Need cleaning')
        response = self.client.post(reverse("tasks:create"), {
            "table": self.table.pk, "task_type": "FULL_RESET", "priority": "HIGH", "due_minutes": 15,
            "assignee": self.waiter.pk, "description": "",
        })
        task = WaiterTask.objects.get()
        self.assertRedirects(response, reverse("tasks:detail", args=[task.pk]))

        # 3. The waiter sees the task, starts it, completes it with a clean check.
        self.client.force_login(self.waiter)
        self.assertContains(self.client.get(reverse("tasks:waiter_home")), task.task_code)
        self.client.post(reverse("tasks:start", args=[task.pk]))
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.CLEANING_IN_PROGRESS)
        response = self.client.post(reverse("tasks:complete", args=[task.pk]), self.answers(CLEAN_CHECK, remarks="Done"))
        self.assertRedirects(response, reverse("tasks:waiter_home"))
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.READY)

        # 4. The receptionist sees T12 ready and books it for guests arriving now.
        self.client.force_login(self.receptionist)
        desk = self.client.get(reverse("reservations:desk"))
        self.assertContains(desk, "T12")
        response = self.client.post(reverse("reservations:walk_in"), {"walkin-table": self.table.pk, "walkin-guests": 3, "walkin-customer_name": "Rao"})
        self.assertRedirects(response, reverse("reservations:desk"))
        self.assertEqual(Reservation.objects.get().status, Reservation.Status.SEATED)
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.OCCUPIED)

    def test_completing_with_dishes_left_keeps_table_dirty(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        task = tasks.create_task(table=self.table, task_type="CLEAR_DISHES", priority="NORMAL", created_by=self.manager, assignee=self.waiter)
        tasks.start(task, user=self.waiter)
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("tasks:complete", args=[task.pk]), self.answers(dict(CLEAN_CHECK, glasses_present=True, ready_for_next=False)))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.VACANT_NEEDS_CLEARING)

    def test_contradictory_check_is_rejected_by_the_form(self):
        self.client.force_login(self.waiter)
        response = self.client.post(reverse("inspections:inspect", args=[self.table.pk]), self.answers(dict(CLEAN_CHECK, plates_present=True)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clear the plates")
        self.assertFalse(TableInspection.objects.exists())

    def test_other_waiter_cannot_inspect_or_see_the_task(self):
        task = tasks.create_task(table=self.table, task_type="CLEAN_TABLE", priority="NORMAL", created_by=self.manager, assignee=self.waiter)
        self.client.force_login(self.waiter2)
        self.assertEqual(self.client.get(reverse("inspections:inspect", args=[self.table.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("tasks:detail", args=[task.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("tasks:start", args=[task.pk])).status_code, 404)

    def test_manager_override_block_and_history(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("tables:override", args=[self.table.pk]), {"override-is_occupied": "1", "override-cleanliness": "CLEAN", "override-reason": "Walk-in seated by hand"})
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.OCCUPIED)
        self.client.post(reverse("tables:block", args=[self.table.pk]), {"blocked": "1", "block-reason": "Broken chair"})
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.BLOCKED)
        page = self.client.get(reverse("tables:detail", args=[self.table.pk]))
        self.assertContains(page, "Override: Walk-in seated by hand")
        self.assertContains(page, "Broken chair")
        self.client.post(reverse("tables:block", args=[self.table.pk]), {"blocked": "0"})
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.OCCUPIED)

    def test_history_survives_deactivation(self):
        inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        table_services.set_active(self.table, active=False, user=self.manager)
        self.assertEqual(TableInspection.objects.filter(table=self.table).count(), 1)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("inspections:list")), "T12")


class ReservationPageTests(StaffCase):
    def booking_data(self, start, **extra):
        local = start.astimezone()
        data = {"date": local.strftime("%Y-%m-%d"), "time": local.strftime("%H:%M"), "guests": 2, "section": "", "duration": "",
                "table": self.table.pk, "customer_name": "Mehta", "phone": "98765 43210", "remarks": ""}
        data.update(extra)
        return data

    def test_receptionist_books_and_double_booking_is_refused(self):
        self.client.force_login(self.receptionist)
        start = at(hours=2).replace(minute=0, second=0, microsecond=0)
        response = self.client.post(reverse("reservations:book"), self.booking_data(start))
        self.assertRedirects(response, reverse("reservations:desk"))
        response = self.client.post(reverse("reservations:book"), self.booking_data(start, customer_name="Other"))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "already booked", status_code=400)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_cancel_frees_the_table(self):
        booking = bookings.create_reservation(restaurant=self.restaurant, table=self.table, customer_name="Mehta", phone="98765 43210",
                                              party_size=2, start_at=at(minutes=15), created_by=self.receptionist)
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.RESERVED)
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reservations:change_status", args=[booking.pk]), {"to_status": "CANCELLED"})
        self.assertEqual(table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk)), TableStatus.READY)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("core:fragment_panels")), "Booking desk")

    def test_waiter_never_sees_customer_names(self):
        bookings.create_reservation(restaurant=self.restaurant, table=self.table, customer_name="Secret Customer", phone="98765 43210",
                                    party_size=2, start_at=at(minutes=15), created_by=self.receptionist)
        self.client.force_login(self.waiter)
        page = self.client.get(reverse("tables:detail", args=[self.table.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Secret Customer")


@override_settings(DETECTION_API_TOKEN="cam-token-123")
class ApiTests(StaffCase):
    def post_json(self, url, payload, **headers):
        return self.client.post(url, json.dumps(payload), content_type="application/json", **headers)

    def detection(self, **overrides):
        payload = {"table_id": str(self.table.public_id), "customers_present": False, "plates_present": True,
                   "glasses_present": False, "confidence": 0.9, "device_id": "cam-1"}
        payload.update(overrides)
        return payload

    def test_detection_requires_token(self):
        self.assertEqual(self.post_json("/api/v1/detections/", self.detection()).status_code, 401)
        self.assertEqual(self.post_json("/api/v1/detections/", self.detection(), HTTP_AUTHORIZATION="Token nope").status_code, 401)
        self.client.force_login(self.manager)  # a session is not enough
        self.assertEqual(self.post_json("/api/v1/detections/", self.detection()).status_code, 401)

    def test_detection_updates_table(self):
        response = self.post_json("/api/v1/detections/", self.detection(), HTTP_AUTHORIZATION="Token cam-token-123")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], TableStatus.VACANT_NEEDS_CLEARING)
        self.assertEqual(TableInspection.objects.get().source, TableInspection.Source.CAMERA)

    def test_detection_validation(self):
        response = self.post_json("/api/v1/detections/", {"table_id": "x"}, HTTP_AUTHORIZATION="Token cam-token-123")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Validation failed.")

    def test_floor_api_is_restaurant_scoped(self):
        self.client.force_login(self.outsider)
        tables = self.client.get("/api/v1/floor/").json()["tables"]
        self.assertEqual([t["number"] for t in tables], [1])
        self.client.logout()
        self.assertEqual(self.client.get("/api/v1/floor/").status_code, 403)

    def test_lookup_api(self):
        self.client.force_login(self.waiter)
        found = self.client.get("/api/v1/tables/lookup/", {"q": str(self.table.public_id)}).json()
        self.assertEqual(found["table"], "T12")
        self.assertIsNotNone(found["inspect_url"])
        self.assertEqual(self.client.get("/api/v1/tables/lookup/", {"q": "T99"}).status_code, 404)
        self.client.force_login(self.waiter2)
        self.assertIsNone(self.client.get("/api/v1/tables/lookup/", {"q": "12"}).json()["inspect_url"])

    def test_reservation_api_conflict_and_roles(self):
        self.client.force_login(self.receptionist)
        payload = {"table": 12, "customer_name": "Mehta", "phone": "98765 43210", "party_size": 2, "start_at": at(hours=3).isoformat()}
        self.assertEqual(self.post_json("/api/v1/reservations/", payload).status_code, 201)
        self.assertEqual(self.post_json("/api/v1/reservations/", payload).status_code, 409)
        availability = self.client.get("/api/v1/reservations/availability/", {"start_at": at(hours=3).isoformat(), "guests": 2}).json()
        self.assertEqual(availability["tables"], [])
        self.client.force_login(self.waiter)
        self.assertEqual(self.post_json("/api/v1/reservations/", payload).status_code, 403)

    def test_session_api_post_needs_csrf(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.receptionist)
        response = client.post("/api/v1/notifications/read/", json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_notifications_api(self):
        tasks.create_task(table=self.table, task_type="CLEAN_TABLE", priority="NORMAL", created_by=self.manager, assignee=self.waiter)
        self.client.force_login(self.waiter)
        data = self.client.get("/api/v1/notifications/").json()
        self.assertEqual(data["unread"], 1)
        self.assertEqual(self.post_json("/api/v1/notifications/read/", {}).json()["unread"], 0)


class WebSocketTests(StaffCase):
    def communicator(self, user):
        communicator = WebsocketCommunicator(LiveConsumer.as_asgi(), "/ws/live/")
        communicator.scope["user"] = user
        return communicator

    async def test_anonymous_rejected(self):
        connected, code = await self.communicator(AnonymousUser()).connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    async def test_table_change_reaches_staff_of_the_restaurant_only(self):
        receptionist = self.communicator(self.receptionist)
        outsider = self.communicator(self.outsider)
        self.assertTrue((await receptionist.connect())[0])
        self.assertTrue((await outsider.connect())[0])

        def inspect():
            with self.captureOnCommitCallbacks(execute=True):
                inspections.record_inspection(self.table, self.waiter, **GUESTS_LEFT_CHECK)
        await database_sync_to_async(inspect)()

        event = await receptionist.receive_json_from(timeout=3)
        self.assertEqual(event["type"], "table.changed")
        self.assertIn("tables", event["topics"])
        self.assertNotIn("customer", json.dumps(event).lower())
        self.assertTrue(await outsider.receive_nothing(timeout=0.5))
        await receptionist.disconnect()
        await outsider.disconnect()

    async def test_task_events_reach_manager_and_assigned_waiter_not_receptionist(self):
        manager, waiter, receptionist = (self.communicator(u) for u in (self.manager, self.waiter, self.receptionist))
        for c in (manager, waiter, receptionist):
            self.assertTrue((await c.connect())[0])

        def create():
            with self.captureOnCommitCallbacks(execute=True):
                tasks.create_task(table=self.table, task_type="CLEAN_TABLE", priority="NORMAL", created_by=self.manager, assignee=self.waiter)
        await database_sync_to_async(create)()

        manager_types = {(await manager.receive_json_from(timeout=3))["type"] for _ in range(2)}
        self.assertEqual(manager_types, {"task.changed"})
        waiter_types = [(await waiter.receive_json_from(timeout=3))["type"] for _ in range(2)]
        self.assertIn("notification", waiter_types)
        self.assertTrue(await receptionist.receive_nothing(timeout=0.5))
        for c in (manager, waiter, receptionist):
            await c.disconnect()


class SeedAndPwaTests(TestCase):
    def test_seed_refuses_without_debug(self):
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())

    @override_settings(DEBUG=True, DEMO_PASSWORD="Demo-pass-123")
    def test_seed_creates_demo_and_is_repeatable(self):
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(RestaurantTable.objects.count(), 20)
        self.assertEqual(WaiterTask.objects.filter(status=WaiterTask.Status.IN_PROGRESS).count(), 1)
        self.assertGreater(Reservation.objects.filter(status=Reservation.Status.COMPLETED).count(), 30)

    def test_manifest_and_service_worker(self):
        self.assertEqual(self.client.get("/manifest.webmanifest").json()["name"], "RestaurantSpy")
        body = self.client.get("/sw.js").content.decode()
        self.assertIn("restaurantspy-static-", body)
        self.assertEqual(body.count("cache.put("), 1)
