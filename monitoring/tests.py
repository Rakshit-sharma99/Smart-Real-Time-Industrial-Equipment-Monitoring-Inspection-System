import json
import random
from decimal import Decimal
from io import StringIO
from unittest import mock

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from core.testutils import make_admin, make_inspector
from equipment.metrics import PRESSURE, TEMPERATURE
from equipment.models import Equipment

from . import services
from .consumers import DashboardConsumer
from .models import Alert, SensorReading, SimulatorSettings
from .simulation import SensorSimulator, primary_metric

NORMAL_VALUES = {"temperature_c": Decimal("40"), "vibration_mm_s": Decimal("3"), "pressure_bar": Decimal("4")}


def make_machine(code="EQ-001", **extra):
    fields = {"name": "Boiler", "equipment_type": Equipment.EquipmentType.BOILER, "location": "Bay"}
    fields.update(extra)
    return Equipment.objects.create(equipment_code=code, **fields)


def send(equipment, temperature=None, **overrides):
    """process_reading with normal values, optionally overriding the temperature."""
    values = dict(NORMAL_VALUES, **overrides)
    if temperature is not None:
        values["temperature_c"] = Decimal(str(temperature))
    return services.process_reading(equipment, **values)


class ProcessReadingTests(TestCase):
    def setUp(self):
        self.machine = make_machine()  # temperature warn 70, crit 85

    def test_stores_reading_and_updates_equipment(self):
        result = send(self.machine)
        self.machine.refresh_from_db()
        self.assertEqual(SensorReading.objects.count(), 1)
        self.assertEqual(self.machine.last_reading_at, result.reading.recorded_at)
        self.assertEqual(self.machine.health_status, Equipment.Health.HEALTHY)
        self.assertEqual(self.machine.display_status, Equipment.Health.HEALTHY)

    def test_exactly_at_warning_is_warning(self):
        send(self.machine, temperature="70.00")
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.WARNING)
        self.assertEqual(Alert.objects.get().severity, Alert.Severity.WARNING)

    def test_exactly_at_critical_is_critical(self):
        send(self.machine, temperature="85.00")
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.CRITICAL)
        self.assertEqual(Alert.objects.get().severity, Alert.Severity.CRITICAL)

    def test_just_below_warning_is_normal(self):
        send(self.machine, temperature="69.99")
        self.assertFalse(Alert.objects.exists())

    def test_worst_metric_wins(self):
        send(self.machine, pressure_bar=Decimal("10.5"))  # pressure critical, temperature normal
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.CRITICAL)

    def test_inactive_equipment_rejected(self):
        self.machine.is_active = False
        self.machine.save()
        with self.assertRaises(ValidationError):
            send(self.machine)
        self.assertFalse(SensorReading.objects.exists())

    def test_negative_value_rejected(self):
        with self.assertRaises(ValidationError):
            send(self.machine, pressure_bar=Decimal("-1"))


class AlertRuleTests(TestCase):
    def setUp(self):
        self.machine = make_machine()

    def test_dedup_updates_existing_alert(self):
        send(self.machine, temperature=72)
        send(self.machine, temperature=78)
        send(self.machine, temperature=74)
        alert = Alert.objects.get()  # still exactly one
        self.assertEqual(alert.first_value, Decimal("72"))
        self.assertEqual(alert.last_value, Decimal("74"))
        self.assertEqual(alert.peak_value, Decimal("78"))

    def test_escalation_warning_to_critical(self):
        send(self.machine, temperature=72)
        result = send(self.machine, temperature=90)
        alert = Alert.objects.get()
        self.assertEqual(alert.severity, Alert.Severity.CRITICAL)
        self.assertIsNotNone(alert.escalated_at)
        self.assertEqual(alert.threshold_value, Decimal("85"))
        self.assertEqual(result.alert_events[0][0], "escalated")

    def test_severity_never_auto_downgrades(self):
        send(self.machine, temperature=90)
        send(self.machine, temperature=72)
        self.assertEqual(Alert.objects.get().severity, Alert.Severity.CRITICAL)

    def test_auto_resolve_after_three_normal_readings(self):
        send(self.machine, temperature=80)
        send(self.machine, temperature=60)
        send(self.machine, temperature=60)
        self.assertTrue(Alert.objects.get().is_open)
        send(self.machine, temperature=60)
        alert = Alert.objects.get()
        self.assertEqual(alert.status, Alert.Status.RESOLVED)
        self.assertEqual(alert.resolution_notes, services.AUTO_RESOLVE_NOTE)
        self.assertIsNone(alert.resolved_by)

    def test_hysteresis_band_does_not_count_as_recovered(self):
        send(self.machine, temperature=80)
        # 67 is below the 70 warning but above 70 * 0.95 = 66.5 -> resets the streak
        for _ in range(5):
            send(self.machine, temperature=67)
        alert = Alert.objects.get()
        self.assertTrue(alert.is_open)
        self.assertEqual(alert.normal_streak, 0)

    def test_breach_resets_recovery_streak(self):
        send(self.machine, temperature=80)
        send(self.machine, temperature=60)
        send(self.machine, temperature=60)
        send(self.machine, temperature=75)  # breach again
        send(self.machine, temperature=60)
        alert = Alert.objects.get()
        self.assertTrue(alert.is_open)
        self.assertEqual(alert.normal_streak, 1)

    def test_new_breach_after_resolve_creates_new_alert(self):
        send(self.machine, temperature=80)
        for _ in range(3):
            send(self.machine, temperature=60)
        send(self.machine, temperature=80)
        self.assertEqual(Alert.objects.count(), 2)
        self.assertEqual(Alert.objects.open().count(), 1)

    def test_description_mentions_simulated_data(self):
        send(self.machine, temperature=80)
        self.assertIn("SIMULATED", Alert.objects.get().description)
        self.assertIn("not a real emergency", Alert.objects.get().description)

    def test_database_allows_only_one_open_alert_per_metric(self):
        send(self.machine, temperature=80)
        existing = Alert.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(
                equipment=self.machine, metric=existing.metric, severity="WARNING",
                first_value=1, last_value=1, peak_value=1, threshold_value=1, description="dup",
            )

    def test_metrics_are_independent(self):
        send(self.machine, temperature=80, pressure_bar=Decimal("9"))
        self.assertEqual(Alert.objects.open().count(), 2)


class AlertActionTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.inspector = make_inspector()
        machine = make_machine()
        send(machine, temperature=80)
        self.alert = Alert.objects.get()

    def test_inspector_can_acknowledge(self):
        services.acknowledge_alert(self.alert, self.inspector)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.ACKNOWLEDGED)
        self.assertEqual(self.alert.acknowledged_by, self.inspector)

    def test_cannot_acknowledge_twice(self):
        services.acknowledge_alert(self.alert, self.inspector)
        with self.assertRaises(services.AlertActionError):
            services.acknowledge_alert(self.alert, self.admin)

    def test_inspector_cannot_resolve(self):
        with self.assertRaises(PermissionDenied):
            services.resolve_alert(self.alert, self.inspector, "fixed")

    def test_resolve_requires_notes(self):
        with self.assertRaises(ValidationError):
            services.resolve_alert(self.alert, self.admin, "   ")

    def test_admin_resolves_and_resolved_is_terminal(self):
        services.resolve_alert(self.alert, self.admin, "Replaced the sensor")
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertEqual(self.alert.resolved_by, self.admin)
        with self.assertRaises(services.AlertActionError):
            services.resolve_alert(self.alert, self.admin, "again")
        with self.assertRaises(services.AlertActionError):
            services.acknowledge_alert(self.alert, self.admin)


@override_settings(SIMULATOR_TOKEN="test-token-123")
class IngestApiTests(TestCase):
    url = "/api/v1/ingest/"

    def setUp(self):
        self.machine = make_machine()

    def post(self, payload, token="test-token-123", **extra):
        headers = {"HTTP_AUTHORIZATION": f"Token {token}"} if token else {}
        return self.client.post(self.url, json.dumps(payload), content_type="application/json", **headers, **extra)

    def payload(self, code="EQ-001", temperature="40.00"):
        return {"readings": [{"equipment_code": code, "temperature_c": temperature,
                              "vibration_mm_s": "3.00", "pressure_bar": "4.00"}]}

    def test_missing_token_rejected(self):
        response = self.post(self.payload(), token=None)
        self.assertEqual(response.status_code, 401)
        self.assertIn("error", response.json())
        self.assertFalse(SensorReading.objects.exists())

    def test_wrong_token_rejected(self):
        response = self.post(self.payload(), token="wrong")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "Invalid simulator token.")

    def test_logged_in_user_without_token_rejected(self):
        self.client.force_login(make_admin())
        self.assertEqual(self.post(self.payload(), token=None).status_code, 401)

    def test_valid_batch_is_processed(self):
        response = self.post(self.payload(temperature="90.00"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["accepted"], 1)
        self.assertEqual(SensorReading.objects.get().source, "SIMULATED")
        self.assertEqual(Alert.objects.get().severity, Alert.Severity.CRITICAL)
        self.assertIsNotNone(SimulatorSettings.load().last_ingest_at)

    def test_unknown_equipment_is_reported(self):
        response = self.post(self.payload(code="EQ-404"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["details"]["rejected"][0]["equipment_code"], "EQ-404")

    def test_invalid_payload_uses_error_format(self):
        response = self.post({"readings": [{"equipment_code": "EQ-001"}]})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["error"], "Validation failed.")
        self.assertIn("readings", body["details"])


class SimulatorTests(TestCase):
    def setUp(self):
        self.machine = make_machine(equipment_type=Equipment.EquipmentType.AIR_COMPRESSOR)  # primary: pressure
        self.simulator = SensorSimulator(rng=random.Random(1))

    def values(self, mode, ticks=20):
        self.machine.sim_mode = mode
        return [self.simulator.next_values(self.machine) for _ in range(ticks)]

    def test_primary_metric_by_type(self):
        self.assertEqual(primary_metric(self.machine), PRESSURE)
        self.assertEqual(primary_metric(make_machine("EQ-2")), TEMPERATURE)

    def test_normal_stays_in_normal_band(self):
        for values in self.values(Equipment.SimMode.NORMAL, 100):
            self.assertGreaterEqual(values["pressure_bar"], Decimal("3.20"))  # 40 % of 8
            self.assertLessEqual(values["pressure_bar"], Decimal("6.40"))  # 80 % of 8

    def test_force_warning_between_limits(self):
        for values in self.values(Equipment.SimMode.FORCE_WARNING):
            self.assertTrue(Decimal("8") <= values["pressure_bar"] < Decimal("10"))
            self.assertLess(values["temperature_c"], Decimal("70"))  # other metrics stay normal

    def test_force_critical_above_critical(self):
        for values in self.values(Equipment.SimMode.FORCE_CRITICAL):
            self.assertTrue(Decimal("10.5") <= values["pressure_bar"] <= Decimal("12"))

    def test_offline_sends_nothing(self):
        self.assertEqual(self.values(Equipment.SimMode.OFFLINE, 3), [None, None, None])

    def test_drift_reaches_critical_then_resets(self):
        pressures = [v["pressure_bar"] for v in self.values(Equipment.SimMode.DRIFT, 40)]
        crit, warn = Decimal("10"), Decimal("8")
        resets = [i for i in range(len(pressures) - 1) if pressures[i] >= crit and pressures[i + 1] < warn]
        self.assertTrue(resets, "DRIFT should reach critical and then drop back to normal")


@override_settings(SIMULATOR_TOKEN="test-token-123")
class SimulateSensorsCommandTests(TestCase):
    def setUp(self):
        make_machine("EQ-001")
        make_machine("EQ-002", sim_mode=Equipment.SimMode.OFFLINE)
        make_machine("EQ-003", is_active=False)

    def test_rejects_bad_interval(self):
        with self.assertRaises(CommandError):
            call_command("simulate_sensors", "--interval", "1", stdout=StringIO())

    def test_rejects_unknown_equipment(self):
        with self.assertRaises(CommandError):
            call_command("simulate_sensors", "--equipment", "EQ-999", stdout=StringIO())

    def test_rejects_bad_url(self):
        with self.assertRaises(CommandError):
            call_command("simulate_sensors", "--url", "localhost", stdout=StringIO())

    @override_settings(SIMULATOR_TOKEN="")
    def test_requires_token(self):
        with self.assertRaises(CommandError):
            call_command("simulate_sensors", "--once", stdout=StringIO())

    def test_once_with_server_down_reports_clearly(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("simulate_sensors", "--once", "--url", "http://127.0.0.1:9", stdout=StringIO())
        self.assertIn("Server not reachable", str(ctx.exception))

    def test_once_sends_active_machines_through_ingest(self):
        """Route the command's HTTP call through Django's test client instead of a real server."""
        client = self.client

        def fake_send(ingest_client, readings):
            response = client.post(
                reverse("api:ingest"), json.dumps({"readings": readings}),
                content_type="application/json", HTTP_AUTHORIZATION="Token test-token-123",
            )
            return response.json()

        out = StringIO()
        with mock.patch("monitoring.management.commands.simulate_sensors.IngestClient.send", fake_send):
            call_command("simulate_sensors", "--once", stdout=out)
        # EQ-002 is OFFLINE and EQ-003 inactive: only EQ-001 sends.
        self.assertEqual(list(SensorReading.objects.values_list("equipment__equipment_code", flat=True)), ["EQ-001"])
        self.assertIn("1 readings accepted", out.getvalue())


class SimulatorSettingsPageTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.machine = make_machine()

    def test_admin_changes_sim_mode(self):
        self.client.force_login(self.admin)
        url = reverse("monitoring:set_sim_mode", args=["EQ-001"])
        self.client.post(url, {"sim_mode": Equipment.SimMode.FORCE_CRITICAL})
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.sim_mode, Equipment.SimMode.FORCE_CRITICAL)

    def test_admin_saves_interval_and_page_shows_state(self):
        self.client.force_login(self.admin)
        url = reverse("monitoring:simulator_settings")
        self.client.post(url, {"interval_seconds": 10, "noise_pct": "1.5"})
        self.assertEqual(SimulatorSettings.load().interval_seconds, 10)
        self.assertContains(self.client.get(url), "Last reading received")

    def test_interval_out_of_range_rejected(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("monitoring:simulator_settings"), {"interval_seconds": 1, "noise_pct": "1"})
        self.assertEqual(SimulatorSettings.load().interval_seconds, 5)


class DashboardConsumerTests(TestCase):
    """WebSocket feed: anonymous users are refused; logged-in users get messages after process_reading()."""

    @classmethod
    def setUpTestData(cls):
        cls.user = make_inspector()
        cls.machine = make_machine()

    def communicator(self, user):
        communicator = WebsocketCommunicator(DashboardConsumer.as_asgi(), "/ws/dashboard/")
        communicator.scope["user"] = user
        return communicator

    async def test_anonymous_connection_rejected(self):
        connected, code = await self.communicator(AnonymousUser()).connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4401)

    def send_breach(self):
        # TestCase never commits, so run the on_commit broadcast callbacks explicitly.
        with self.captureOnCommitCallbacks(execute=True):
            send(self.machine, temperature=90)

    async def test_authenticated_client_receives_reading_and_alert(self):
        communicator = self.communicator(self.user)
        connected, _ = await communicator.connect()
        self.assertTrue(connected)

        await database_sync_to_async(self.send_breach)()

        reading = await communicator.receive_json_from(timeout=3)
        self.assertEqual(reading["type"], "reading")
        self.assertEqual(reading["equipment"]["code"], "EQ-001")
        self.assertEqual(reading["equipment"]["status"], "CRITICAL")
        self.assertEqual(reading["values"]["temperature"], "90.00")
        self.assertEqual(reading["source"], "SIMULATED")
        self.assertEqual(reading["summary"]["active_alerts"], 1)

        alert = await communicator.receive_json_from(timeout=3)
        self.assertEqual(alert["type"], "alert")
        self.assertEqual(alert["event"], "created")
        self.assertEqual(alert["alert"]["severity"], "CRITICAL")
        self.assertEqual(alert["alert"]["metric"], "temperature")
        # No user data in the payload.
        self.assertNotIn(self.user.username, json.dumps([reading, alert]))
        await communicator.disconnect()


class ReadApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_inspector()
        cls.machine = make_machine()
        make_machine("EQ-009", is_active=False)

    def setUp(self):
        send(self.machine, temperature=72)
        send(self.machine, temperature=73)

    def test_requires_login(self):
        for url in ["/api/v1/dashboard/summary/", "/api/v1/equipment/status/", "/api/v1/equipment/EQ-001/readings/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                self.assertIn("error", response.json())

    def test_dashboard_summary(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/v1/dashboard/summary/").json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["warning"], 1)
        self.assertEqual(data["active_alerts"], 1)

    def test_equipment_status_lists_active_machines_with_latest_values(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/v1/equipment/status/").json()
        self.assertEqual(data["count"], 1)  # paginated; inactive EQ-009 excluded
        item = data["results"][0]
        self.assertEqual(item["code"], "EQ-001")
        self.assertEqual(item["status"], "WARNING")
        self.assertEqual(item["values"]["temperature"], "73.00")

    def test_readings_oldest_first_with_thresholds_and_limit(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/v1/equipment/EQ-001/readings/?limit=1").json()
        self.assertEqual(len(data["readings"]), 1)
        self.assertEqual(data["readings"][0]["temperature"], "73.00")
        self.assertEqual(data["thresholds"]["temperature"]["warn"], "70.00")
        data = self.client.get("/api/v1/equipment/EQ-001/readings/").json()
        self.assertEqual([r["temperature"] for r in data["readings"]], ["72.00", "73.00"])

    def test_unknown_equipment_is_json_404(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/equipment/EQ-404/readings/")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())


class LivePagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_inspector()
        cls.machine = make_machine()

    def setUp(self):
        send(self.machine, temperature=72)
        self.client.force_login(self.user)

    def test_dashboard_shows_kpis_and_live_hooks(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'data-kpi="warning"')
        self.assertContains(response, 'class="js-live-status" data-code="EQ-001"')
        self.assertContains(response, 'id="dashboard-data"')

    def test_live_monitoring_page(self):
        response = self.client.get(reverse("monitoring:live"), {"eq": "EQ-001", "metric": "pressure"})
        self.assertContains(response, 'data-selected="EQ-001"')
        self.assertContains(response, 'data-metric="pressure"')
        self.assertContains(response, "72.00")


class AlertPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector()
        cls.machine = make_machine()

    def setUp(self):
        send(self.machine, temperature=80)
        self.alert = Alert.objects.get()

    def test_list_shows_open_alerts_by_default(self):
        old = Alert.objects.create(
            equipment=self.machine, metric="vibration", severity="WARNING", status="RESOLVED",
            first_value=8, last_value=8, peak_value=8, threshold_value="7.10", description="old",
        )
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("alerts:list"))
        self.assertContains(response, self.alert.reference)
        self.assertNotContains(response, old.reference)
        response = self.client.get(reverse("alerts:list"), {"status": "all"})
        self.assertContains(response, old.reference)
        response = self.client.get(reverse("alerts:list"), {"status": "all", "severity": "CRITICAL"})
        self.assertNotContains(response, self.alert.reference)

    def test_detail_page(self):
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("alerts:detail", args=[self.alert.pk]))
        self.assertContains(response, "not a real emergency")
        self.assertContains(response, reverse("alerts:acknowledge", args=[self.alert.pk]))
        self.assertNotContains(response, reverse("alerts:resolve", args=[self.alert.pk]))  # inspector: no resolve form
        self.assertEqual(self.client.get(reverse("alerts:detail", args=[999])).status_code, 404)

    def test_inspector_acknowledges_from_page(self):
        self.client.force_login(self.inspector)
        response = self.client.post(reverse("alerts:acknowledge", args=[self.alert.pk]),
                                    {"next": reverse("alerts:list")})
        self.assertRedirects(response, reverse("alerts:list"))
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.ACKNOWLEDGED)

    def test_acknowledge_ignores_external_next_url(self):
        self.client.force_login(self.inspector)
        response = self.client.post(reverse("alerts:acknowledge", args=[self.alert.pk]), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("alerts:detail", args=[self.alert.pk]))

    def test_inspector_cannot_resolve_from_page(self):
        self.client.force_login(self.inspector)
        response = self.client.post(reverse("alerts:resolve", args=[self.alert.pk]), {"resolution_notes": "x"})
        self.assertEqual(response.status_code, 403)
        self.alert.refresh_from_db()
        self.assertTrue(self.alert.is_open)

    def test_admin_resolve_requires_notes(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("alerts:resolve", args=[self.alert.pk]), {"resolution_notes": ""})
        self.assertEqual(response.status_code, 400)
        self.alert.refresh_from_db()
        self.assertTrue(self.alert.is_open)

    def test_admin_resolves_from_page(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("alerts:resolve", args=[self.alert.pk]), {"resolution_notes": "Valve replaced"})
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.RESOLVED)
        self.assertEqual(self.alert.resolution_notes, "Valve replaced")

    def test_equipment_page_has_alerts_tab(self):
        self.client.force_login(self.inspector)
        self.assertContains(self.client.get(self.machine.get_absolute_url()), self.alert.reference)


class AlertApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector()
        cls.machine = make_machine()

    def setUp(self):
        send(self.machine, temperature=80)
        self.alert = Alert.objects.get()

    def api_post(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_list_filters(self):
        self.client.force_login(self.inspector)
        data = self.client.get("/api/v1/alerts/").json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["results"][0]["severity"], "WARNING")
        self.assertEqual(self.client.get("/api/v1/alerts/?status=RESOLVED").json()["count"], 0)
        self.assertEqual(self.client.get("/api/v1/alerts/?equipment=EQ-001&metric=temperature").json()["count"], 1)

    def test_acknowledge_any_role_once(self):
        self.client.force_login(self.inspector)
        url = f"/api/v1/alerts/{self.alert.pk}/acknowledge/"
        response = self.api_post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ACKNOWLEDGED")
        response = self.api_post(url)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "Only active alerts can be acknowledged.")

    def test_resolve_admin_only(self):
        url = f"/api/v1/alerts/{self.alert.pk}/resolve/"
        self.client.force_login(self.inspector)
        self.assertEqual(self.api_post(url, {"resolution_notes": "x"}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.api_post(url, {"resolution_notes": ""})
        self.assertEqual(response.status_code, 400)
        self.assertIn("resolution_notes", response.json()["details"])
        response = self.api_post(url, {"resolution_notes": "Sensor recalibrated"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "RESOLVED")
        self.assertEqual(self.api_post(url, {"resolution_notes": "again"}).status_code, 409)

    def test_session_post_needs_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.inspector)
        response = client.post(f"/api/v1/alerts/{self.alert.pk}/acknowledge/")
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["error"])
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.ACTIVE)

    def test_unknown_alert_404(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.api_post("/api/v1/alerts/999/acknowledge/").status_code, 404)
