import io
import re
import shutil
import tempfile
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from django.core.files.base import ContentFile

from core.testutils import make_admin, make_inspector
from equipment.models import Equipment
from inspections import services as inspection_services
from inspections.models import InspectionResponse, InspectionTemplate
from monitoring.services import process_reading

from . import services

TEMP_MEDIA = tempfile.mkdtemp(prefix="equipwatch-report-media-")


def page_count(pdf_bytes):
    return len(re.findall(rb"/Type /Page\b", pdf_bytes))


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector("ivan")
        cls.other = make_inspector("isha")
        cls.machine = Equipment.objects.create(
            equipment_code="EQ-001", name="Air Compressor", equipment_type=Equipment.EquipmentType.AIR_COMPRESSOR,
            location="Compressor House",
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def submitted_inspection(self, items=3, inspector=None, with_photo=False):
        inspector = inspector or self.inspector
        template = InspectionTemplate.objects.create(name=f"T{InspectionTemplate.objects.count()}")
        for order in range(1, items + 1):
            template.items.create(order=order, text=f"Check item number {order} & <special> chars")
        inspection = inspection_services.assign_inspection(
            equipment=self.machine, template=template, inspector=inspector, due_date=None, created_by=self.admin
        )
        inspection_services.start_inspection(inspection, inspector)
        responses = list(inspection.responses.all())
        for response in responses:
            response.outcome = InspectionResponse.Outcome.PASS
            response.save()
        first = responses[0]
        first.outcome, first.remarks = InspectionResponse.Outcome.FAIL, "Leak at the drain valve"
        if with_photo:
            buffer = io.BytesIO()
            Image.new("RGB", (800, 600), (10, 120, 200)).save(buffer, format="JPEG")
            first.photo.save("p.jpg", ContentFile(buffer.getvalue()), save=False)
        first.save()
        inspection_services.submit_inspection(inspection, inspector)
        return inspection

    def test_inspection_pdf_download(self):
        inspection = self.submitted_inspection(with_photo=True)
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("reports:inspection_pdf", args=[inspection.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertRegex(response["Content-Disposition"], r'attachment; filename="inspection_EQ-001_\d{4}-\d{2}-\d{2}\.pdf"')

    def test_page_numbers_and_long_tables_split(self):
        inspection = self.submitted_inspection(items=70)
        pdf = services.build_inspection_report(inspection, compress=False)
        pages = page_count(pdf)
        self.assertGreater(pages, 1)
        self.assertIn(f"Page 1 of {pages}".encode(), pdf)
        self.assertIn(f"Page {pages} of {pages}".encode(), pdf)

    def test_missing_photo_file_shows_placeholder(self):
        inspection = self.submitted_inspection(with_photo=True)
        response = inspection.responses.exclude(photo="").get()
        response.photo.storage.delete(response.photo.name)
        pdf = services.build_inspection_report(inspection, compress=False)
        self.assertIn(b"[photo missing]", pdf)

    def test_other_inspectors_report_is_404(self):
        theirs = self.submitted_inspection(inspector=self.other)
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(reverse("reports:inspection_pdf", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("reports:inspection_pdf", args=[99999])).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("reports:inspection_pdf", args=[theirs.pk])).status_code, 200)

    def test_anonymous_redirected(self):
        response = self.client.get(reverse("reports:health_pdf", args=["EQ-001"]))
        self.assertEqual(response.status_code, 302)

    def test_health_pdf_for_both_roles(self):
        process_reading(self.machine, temperature_c=Decimal("90"), vibration_mm_s=Decimal("3"), pressure_bar=Decimal("4"))
        for user in [self.inspector, self.admin]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("reports:health_pdf", args=["EQ-001"]), {"days": 7})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.content.startswith(b"%PDF"))
                self.assertRegex(response["Content-Disposition"], r'filename="health_EQ-001_\d{4}-\d{2}-\d{2}\.pdf"')

    def test_health_pdf_mentions_simulated_data(self):
        process_reading(self.machine, temperature_c=Decimal("40"), vibration_mm_s=Decimal("3"), pressure_bar=Decimal("4"))
        pdf = services.build_health_report(self.machine, self.admin, days=7, compress=False)
        self.assertIn(b"Contains SIMULATED sensor data", pdf)
        self.assertIn(b"1 readings recorded", pdf)

    def test_days_are_clamped(self):
        self.assertEqual(services.clamp_days("500"), 90)
        self.assertEqual(services.clamp_days("0"), 1)
        self.assertEqual(services.clamp_days("abc"), 7)
        self.assertEqual(services.clamp_days(None), 7)

    def test_filenames_are_sanitised(self):
        self.assertEqual(services.safe_filename("inspection", "EQ 001/../x", "2026-10-29"), "inspection_EQ_001_.._x_2026-10-29.pdf")

    def test_reports_page_lists_only_visible_inspections(self):
        mine = self.submitted_inspection()
        theirs = self.submitted_inspection(inspector=self.other)
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("reports:home"))
        self.assertContains(response, mine.reference)
        self.assertNotContains(response, theirs.reference)
