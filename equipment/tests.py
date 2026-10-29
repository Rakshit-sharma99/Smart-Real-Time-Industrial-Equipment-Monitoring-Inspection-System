import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.testutils import make_admin, make_inspector

from .metrics import CRITICAL, NORMAL, WARNING, classify, worst
from .models import Equipment


def make_equipment(code="EQ-100", **extra):
    defaults = {
        "name": f"Machine {code}",
        "equipment_type": Equipment.EquipmentType.AIR_COMPRESSOR,
        "location": "Bay A",
    }
    defaults.update(extra)
    return Equipment.objects.create(equipment_code=code, **defaults)


class ThresholdValidationTests(TestCase):
    def test_defaults_are_valid(self):
        make_equipment().full_clean()

    def test_warning_must_be_below_critical(self):
        equipment = make_equipment()
        equipment.temp_warn = Decimal("90")
        equipment.temp_crit = Decimal("85")
        with self.assertRaises(ValidationError) as ctx:
            equipment.full_clean()
        self.assertIn("temp_crit", ctx.exception.message_dict)

    def test_equal_warning_and_critical_rejected(self):
        equipment = make_equipment()
        equipment.vib_warn = equipment.vib_crit = Decimal("5")
        with self.assertRaises(ValidationError) as ctx:
            equipment.full_clean()
        self.assertIn("vib_crit", ctx.exception.message_dict)

    def test_warning_must_be_positive(self):
        equipment = make_equipment()
        equipment.pres_warn = Decimal("0")
        with self.assertRaises(ValidationError) as ctx:
            equipment.full_clean()
        self.assertIn("pres_warn", ctx.exception.message_dict)

    def test_database_constraint_blocks_invalid_thresholds(self):
        equipment = make_equipment()
        equipment.temp_crit = Decimal("10")  # below the 70 warning
        with self.assertRaises(IntegrityError), transaction.atomic():
            equipment.save()


class ClassifyTests(TestCase):
    def test_boundaries(self):
        warn, crit = Decimal("70"), Decimal("85")
        self.assertEqual(classify(Decimal("69.99"), warn, crit), NORMAL)
        self.assertEqual(classify(Decimal("70.00"), warn, crit), WARNING)  # exactly at warning
        self.assertEqual(classify(Decimal("84.99"), warn, crit), WARNING)
        self.assertEqual(classify(Decimal("85.00"), warn, crit), CRITICAL)  # exactly at critical

    def test_worst(self):
        self.assertEqual(worst([NORMAL, CRITICAL, WARNING]), CRITICAL)
        self.assertEqual(worst([]), NORMAL)


class DisplayStatusTests(TestCase):
    def test_never_read_is_offline(self):
        self.assertEqual(make_equipment().display_status, Equipment.OFFLINE)

    def test_recent_reading_shows_health(self):
        equipment = make_equipment(last_reading_at=timezone.now(), health_status=Equipment.Health.WARNING)
        self.assertEqual(equipment.display_status, Equipment.Health.WARNING)

    def test_old_reading_is_offline(self):
        equipment = make_equipment(last_reading_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(equipment.display_status, Equipment.OFFLINE)

    def test_inactive(self):
        self.assertEqual(make_equipment(is_active=False).display_status, Equipment.INACTIVE)

    def test_status_counts_exclude_inactive(self):
        make_equipment("EQ-1", last_reading_at=timezone.now())
        make_equipment("EQ-2", last_reading_at=timezone.now(), health_status=Equipment.Health.CRITICAL)
        make_equipment("EQ-3")  # offline
        make_equipment("EQ-4", is_active=False)
        counts = Equipment.objects.status_counts()
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["healthy"], 1)
        self.assertEqual(counts["critical"], 1)
        self.assertEqual(counts["offline"], 1)
        self.assertEqual(counts["inactive"], 1)


class EquipmentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector()
        cls.equipment = make_equipment("EQ-001", name="Air Compressor", location="Compressor House")
        make_equipment("EQ-002", name="Boiler", equipment_type=Equipment.EquipmentType.BOILER, location="Boiler House")

    def create_payload(self, **overrides):
        payload = {
            "equipment_code": "eq-050",
            "name": "New Pump",
            "equipment_type": Equipment.EquipmentType.HYDRAULIC_PUMP,
            "location": "Press Shop",
            "installed_on": "",
            "description": "",
            "temp_warn": "70", "temp_crit": "85",
            "vib_warn": "7.1", "vib_crit": "11",
            "pres_warn": "8", "pres_crit": "10",
        }
        payload.update(overrides)
        return payload

    def test_admin_creates_equipment(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("equipment:create"), self.create_payload())
        equipment = Equipment.objects.get(equipment_code="EQ-050")  # upper-cased
        self.assertRedirects(response, equipment.get_absolute_url())
        self.assertIsNotNone(equipment.public_id)

    def test_create_rejects_invalid_thresholds(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("equipment:create"), self.create_payload(temp_warn="90", temp_crit="80")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Equipment.objects.filter(equipment_code="EQ-050").exists())
        self.assertContains(response, "critical limit must be greater")

    def test_threshold_edit(self):
        self.client.force_login(self.admin)
        url = reverse("equipment:thresholds", args=["EQ-001"])
        payload = {"temp_warn": "60", "temp_crit": "75", "vib_warn": "7.1", "vib_crit": "11",
                   "pres_warn": "8", "pres_crit": "10"}
        self.assertRedirects(self.client.post(url, payload), self.equipment.get_absolute_url())
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.temp_warn, Decimal("60"))

    def test_inspector_cannot_create_or_edit(self):
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.post(reverse("equipment:create"), self.create_payload()).status_code, 403)
        self.assertEqual(self.client.get(reverse("equipment:edit", args=["EQ-001"])).status_code, 403)
        self.assertEqual(self.client.post(reverse("equipment:set_active", args=["EQ-001"])).status_code, 403)
        self.assertFalse(Equipment.objects.filter(equipment_code="EQ-050").exists())

    def test_inspector_can_view_list_and_detail(self):
        self.client.force_login(self.inspector)
        self.assertContains(self.client.get(reverse("equipment:list")), "EQ-001")
        response = self.client.get(reverse("equipment:detail", args=["EQ-001"]))
        self.assertContains(response, "Air Compressor")
        self.assertNotContains(response, reverse("equipment:edit", args=["EQ-001"]))

    def test_deactivate_and_reactivate(self):
        self.client.force_login(self.admin)
        url = reverse("equipment:set_active", args=["EQ-001"])
        self.client.post(url, {"active": "0"})
        self.equipment.refresh_from_db()
        self.assertFalse(self.equipment.is_active)
        self.client.post(url, {"active": "1"})
        self.equipment.refresh_from_db()
        self.assertTrue(self.equipment.is_active)

    def test_search_and_filters(self):
        self.client.force_login(self.inspector)
        url = reverse("equipment:list")
        response = self.client.get(url, {"q": "boiler"})
        self.assertContains(response, "EQ-002")
        self.assertNotContains(response, "EQ-001")
        response = self.client.get(url, {"equipment_type": Equipment.EquipmentType.AIR_COMPRESSOR})
        self.assertContains(response, "EQ-001")
        self.assertNotContains(response, "EQ-002")
        response = self.client.get(url, {"location": "Boiler House"})
        self.assertNotContains(response, "EQ-001")
        response = self.client.get(url, {"status": Equipment.OFFLINE})
        self.assertContains(response, "EQ-001")
        response = self.client.get(url, {"status": Equipment.Health.HEALTHY})
        self.assertNotContains(response, "EQ-001")

    def test_missing_equipment_is_404(self):
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(reverse("equipment:detail", args=["EQ-999"])).status_code, 404)


class QrTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector()
        cls.equipment = make_equipment("EQ-001")

    def test_qr_png_inline_for_any_role(self):
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("equipment:qr_png", args=["EQ-001"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_only_admin_downloads_qr(self):
        url = reverse("equipment:qr_png", args=["EQ-001"]) + "?download=1"
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_labels_admin_only(self):
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(reverse("equipment:label", args=["EQ-001"])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("equipment:label", args=["EQ-001"])), "EQ-001")
        sheet = self.client.get(reverse("equipment:label_sheet"), {"codes": ["EQ-001"]})
        self.assertContains(sheet, "EQ-001")

    def test_qr_encodes_redirect_url(self):
        self.assertTrue(self.equipment.qr_target_url.endswith(f"/e/{self.equipment.public_id}/"))

    def test_valid_uuid_redirects_to_detail(self):
        self.client.force_login(self.inspector)
        response = self.client.get(f"/e/{self.equipment.public_id}/")
        self.assertRedirects(response, self.equipment.get_absolute_url())

    def test_unknown_or_invalid_uuid_is_404(self):
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(f"/e/{uuid.uuid4()}/").status_code, 404)
        self.assertEqual(self.client.get("/e/not-a-uuid/").status_code, 404)

    def test_anonymous_redirected_to_login(self):
        url = f"/e/{self.equipment.public_id}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertIn("next=", response["Location"])


class LookupApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_inspector()
        cls.equipment = make_equipment("EQ-001", name="Air Compressor")

    def test_requires_login(self):
        response = self.client.get("/api/v1/equipment/lookup/?q=EQ-001")
        self.assertEqual(response.status_code, 403)

    def test_by_code_case_insensitive(self):
        self.client.force_login(self.user)
        data = self.client.get("/api/v1/equipment/lookup/?q=eq-001").json()
        self.assertEqual(data["code"], "EQ-001")
        self.assertEqual(data["url"], self.equipment.get_absolute_url())

    def test_by_uuid(self):
        self.client.force_login(self.user)
        data = self.client.get(f"/api/v1/equipment/lookup/?q={self.equipment.public_id}").json()
        self.assertEqual(data["code"], "EQ-001")

    def test_unknown_is_404(self):
        self.client.force_login(self.user)
        for query in ["EQ-999", str(uuid.uuid4())]:
            with self.subTest(q=query):
                response = self.client.get(f"/api/v1/equipment/lookup/?q={query}")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"], "Unknown equipment")

    def test_empty_query_is_400(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/v1/equipment/lookup/?q=").status_code, 400)

    def test_scanner_page_for_both_roles(self):
        for user in [self.user, make_admin()]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("inspections:scanner"))
                self.assertContains(response, "html5-qrcode@2.3.8")
                self.assertContains(response, 'integrity="sha384-')
                self.assertContains(response, "Scan from image file")
