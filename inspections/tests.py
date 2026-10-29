import io
import shutil
import tempfile
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.testutils import make_admin, make_inspector
from equipment.models import Equipment
from monitoring.services import process_reading

from . import services
from .models import Defect, Inspection, InspectionResponse, InspectionTemplate

Outcome = InspectionResponse.Outcome
TEMP_MEDIA = tempfile.mkdtemp(prefix="equipwatch-test-media-")


def image_bytes(fmt="JPEG", size=(2400, 1200), exif=None):
    buffer = io.BytesIO()
    image = Image.new("RGB", size, (200, 80, 40))
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def upload(name="photo.jpg", content=None, content_type="image/jpeg"):
    return SimpleUploadedFile(name, content if content is not None else image_bytes(), content_type=content_type)


class InspectionTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector("ivan")
        cls.other_inspector = make_inspector("isha")
        cls.machine = Equipment.objects.create(
            equipment_code="EQ-001", name="Pump", equipment_type=Equipment.EquipmentType.HYDRAULIC_PUMP,
            location="Bay A",
        )
        cls.template = InspectionTemplate.objects.create(name="General")
        for order, text in enumerate(["Guards in place", "No leaks", "E-stop works"], start=1):
            cls.template.items.create(order=order, text=text)

    def assign(self, inspector=None):
        return services.assign_inspection(
            equipment=self.machine, template=self.template, inspector=inspector or self.inspector,
            due_date=None, created_by=self.admin,
        )

    def started(self, inspector=None):
        inspection = self.assign(inspector)
        services.start_inspection(inspection, inspector or self.inspector)
        inspection.refresh_from_db()
        return inspection

    def answer(self, inspection, *outcomes, remarks="checked"):
        for response, outcome in zip(inspection.responses.all(), outcomes):
            response.outcome = outcome
            response.remarks = remarks
            response.save()


class ResultRuleTests(TestCase):
    def test_any_fail_is_fail(self):
        self.assertEqual(services.overall_result([Outcome.PASS, Outcome.FAIL, Outcome.NEEDS_ATTENTION]), "FAIL")

    def test_attention_without_fail(self):
        self.assertEqual(services.overall_result([Outcome.PASS, Outcome.NEEDS_ATTENTION]), "ATTENTION")

    def test_na_is_ignored(self):
        self.assertEqual(services.overall_result([Outcome.PASS, Outcome.NOT_APPLICABLE]), "PASS")

    def test_all_na_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.overall_result([Outcome.NOT_APPLICABLE, Outcome.NOT_APPLICABLE])


class InspectionFlowTests(InspectionTestBase):
    def test_start_snapshots_items(self):
        inspection = self.started()
        self.assertEqual(inspection.status, Inspection.Status.IN_PROGRESS)
        self.assertEqual(list(inspection.responses.values_list("item_text", flat=True)),
                         ["Guards in place", "No leaks", "E-stop works"])
        # Editing the template later does not change the inspection's history.
        self.template.items.filter(order=1).update(text="Changed text")
        self.assertEqual(inspection.responses.first().item_text, "Guards in place")

    def test_only_assignee_can_start(self):
        inspection = self.assign()
        with self.assertRaises(PermissionDenied):
            services.start_inspection(inspection, self.other_inspector)

    def test_submit_sets_result_and_creates_defects(self):
        inspection = self.started()
        self.answer(inspection, Outcome.FAIL, Outcome.NEEDS_ATTENTION, Outcome.PASS)
        services.submit_inspection(inspection, self.inspector)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.Status.SUBMITTED)
        self.assertEqual(inspection.overall_result, Inspection.Result.FAIL)
        self.assertIsNotNone(inspection.submitted_at)
        severities = sorted(Defect.objects.values_list("severity", flat=True))
        self.assertEqual(severities, [Defect.Severity.HIGH, Defect.Severity.MEDIUM])

    def test_high_defect_raises_healthy_machine_to_warning(self):
        process_reading(self.machine, temperature_c=Decimal("40"), vibration_mm_s=Decimal("3"), pressure_bar=Decimal("4"))
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.HEALTHY)
        inspection = self.started()
        self.answer(inspection, Outcome.FAIL, Outcome.PASS, Outcome.PASS)
        services.submit_inspection(inspection, self.inspector)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.WARNING)

    def test_remarks_required_for_fail_and_attention(self):
        inspection = self.started()
        self.answer(inspection, Outcome.FAIL, Outcome.PASS, Outcome.PASS, remarks="")
        with self.assertRaises(ValidationError) as ctx:
            services.submit_inspection(inspection, self.inspector)
        self.assertIn("remarks are required", " ".join(ctx.exception.messages))

    def test_unanswered_items_block_submit(self):
        inspection = self.started()
        self.answer(inspection, Outcome.PASS)
        with self.assertRaises(ValidationError):
            services.submit_inspection(inspection, self.inspector)

    def test_all_na_rejected_on_submit(self):
        inspection = self.started()
        self.answer(inspection, *[Outcome.NOT_APPLICABLE] * 3)
        with self.assertRaises(ValidationError):
            services.submit_inspection(inspection, self.inspector)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.Status.IN_PROGRESS)

    def test_double_submit_is_refused(self):
        inspection = self.started()
        self.answer(inspection, Outcome.FAIL, Outcome.PASS, Outcome.PASS)
        services.submit_inspection(inspection, self.inspector)
        with self.assertRaises(services.InspectionError):
            services.submit_inspection(inspection, self.inspector)
        self.assertEqual(Defect.objects.count(), 1)

    def test_other_inspector_cannot_submit(self):
        inspection = self.started()
        self.answer(inspection, Outcome.PASS, Outcome.PASS, Outcome.PASS)
        with self.assertRaises(PermissionDenied):
            services.submit_inspection(inspection, self.other_inspector)


class InspectionViewTests(InspectionTestBase):
    def perform_payload(self, inspection, outcomes, action="save", remarks="ok"):
        responses = list(inspection.responses.all())
        data = {
            "form-TOTAL_FORMS": len(responses), "form-INITIAL_FORMS": len(responses),
            "form-MIN_NUM_FORMS": 0, "form-MAX_NUM_FORMS": 1000,
            "remarks": "", "action": action,
        }
        for i, (response, outcome) in enumerate(zip(responses, outcomes)):
            data[f"form-{i}-id"] = response.pk
            data[f"form-{i}-outcome"] = outcome
            data[f"form-{i}-remarks"] = remarks
        return data

    def test_inspector_sees_only_own_inspections(self):
        mine = self.assign()
        theirs = self.assign(self.other_inspector)
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("inspections:list"))
        self.assertContains(response, mine.reference)
        self.assertNotContains(response, theirs.reference)
        self.assertEqual(self.client.get(reverse("inspections:detail", args=[theirs.pk])).status_code, 404)

    def test_admin_sees_all(self):
        theirs = self.assign(self.other_inspector)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("inspections:detail", args=[theirs.pk])), theirs.reference)

    def test_save_progress_then_submit_through_the_page(self):
        inspection = self.started()
        self.client.force_login(self.inspector)
        url = reverse("inspections:perform", args=[inspection.pk])
        response = self.client.post(url, self.perform_payload(inspection, ["PASS", "", ""]))
        self.assertRedirects(response, url)
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.Status.IN_PROGRESS)
        self.assertEqual(inspection.responses.first().outcome, "PASS")

        response = self.client.post(url, self.perform_payload(inspection, ["PASS", "FAIL", "PASS"], action="submit"))
        self.assertRedirects(response, reverse("inspections:detail", args=[inspection.pk]))
        inspection.refresh_from_db()
        self.assertEqual(inspection.overall_result, Inspection.Result.FAIL)

    def test_submit_with_missing_answers_shows_errors_and_keeps_answers(self):
        inspection = self.started()
        self.client.force_login(self.inspector)
        url = reverse("inspections:perform", args=[inspection.pk])
        response = self.client.post(url, self.perform_payload(inspection, ["PASS", "", ""], action="submit"))
        self.assertContains(response, "Item 2 has no answer.")
        inspection.refresh_from_db()
        self.assertEqual(inspection.status, Inspection.Status.IN_PROGRESS)
        self.assertEqual(inspection.responses.first().outcome, "PASS")

    def test_fail_without_remarks_is_a_form_error(self):
        inspection = self.started()
        self.client.force_login(self.inspector)
        url = reverse("inspections:perform", args=[inspection.pk])
        response = self.client.post(url, self.perform_payload(inspection, ["FAIL", "PASS", "PASS"], remarks=""))
        self.assertContains(response, "Remarks are required")

    def test_submitted_inspection_is_immutable(self):
        inspection = self.started()
        self.answer(inspection, Outcome.PASS, Outcome.PASS, Outcome.PASS)
        services.submit_inspection(inspection, self.inspector)
        self.client.force_login(self.inspector)
        url = reverse("inspections:perform", args=[inspection.pk])
        self.assertRedirects(self.client.get(url), reverse("inspections:detail", args=[inspection.pk]))
        self.client.post(url, self.perform_payload(inspection, ["FAIL", "FAIL", "FAIL"], remarks="changed"))
        self.assertEqual(set(inspection.responses.values_list("outcome", flat=True)), {"PASS"})
        with self.assertRaises(services.InspectionError):
            services.start_inspection(inspection, self.inspector)

    def test_non_assignee_cannot_perform(self):
        inspection = self.started()
        self.client.force_login(self.admin)  # admin can see it, but is not the assignee
        self.assertEqual(self.client.get(reverse("inspections:perform", args=[inspection.pk])).status_code, 403)
        self.client.force_login(self.other_inspector)
        self.assertEqual(self.client.get(reverse("inspections:perform", args=[inspection.pk])).status_code, 404)

    def test_admin_assigns_inspection(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("inspections:assign"), {
            "equipment": self.machine.pk, "template": self.template.pk, "inspector": self.inspector.pk, "due_date": "",
        })
        inspection = Inspection.objects.get()
        self.assertRedirects(response, reverse("inspections:detail", args=[inspection.pk]))
        self.assertEqual(inspection.status, Inspection.Status.ASSIGNED)

    def test_inspector_cannot_assign_or_edit_templates(self):
        self.client.force_login(self.inspector)
        for url in [reverse("inspections:assign"), reverse("inspections:template_list"),
                    reverse("inspections:template_create"), reverse("inspections:template_edit", args=[self.template.pk])]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_edits_template_items(self):
        self.client.force_login(self.admin)
        items = list(self.template.items.all())
        data = {
            "name": "General", "equipment_type": "", "description": "", "is_active": "on",
            "items-TOTAL_FORMS": 4, "items-INITIAL_FORMS": 3, "items-MIN_NUM_FORMS": 1, "items-MAX_NUM_FORMS": 1000,
        }
        for i, item in enumerate(items):
            data.update({f"items-{i}-id": item.pk, f"items-{i}-order": item.order, f"items-{i}-text": item.text})
        data["items-2-DELETE"] = "on"
        data.update({"items-3-order": 9, "items-3-text": "New item"})
        response = self.client.post(reverse("inspections:template_edit", args=[self.template.pk]), data)
        self.assertRedirects(response, reverse("inspections:template_list"))
        self.assertEqual(list(self.template.items.values_list("text", flat=True)),
                         ["Guards in place", "No leaks", "New item"])


class AdhocTests(InspectionTestBase):
    def test_inspector_starts_adhoc(self):
        self.client.force_login(self.inspector)
        response = self.client.post(reverse("inspections:start_adhoc", args=["EQ-001"]))
        inspection = Inspection.objects.get()
        self.assertRedirects(response, reverse("inspections:perform", args=[inspection.pk]))
        self.assertTrue(inspection.is_adhoc)
        self.assertEqual(inspection.responses.count(), 3)

    def test_not_allowed_with_open_assignment(self):
        self.assign()
        with self.assertRaises(services.InspectionError):
            services.start_adhoc_inspection(self.machine, self.inspector)

    @override_settings(ALLOW_ADHOC_INSPECTIONS=False)
    def test_setting_turns_adhoc_off(self):
        with self.assertRaises(services.InspectionError):
            services.start_adhoc_inspection(self.machine, self.inspector)

    def test_admin_cannot_start_adhoc(self):
        with self.assertRaises(PermissionDenied):
            services.start_adhoc_inspection(self.machine, self.admin)

    def test_needs_a_template(self):
        self.template.is_active = False
        self.template.save()
        with self.assertRaises(services.InspectionError):
            services.start_adhoc_inspection(self.machine, self.inspector)

    def test_type_specific_template_preferred(self):
        pump = InspectionTemplate.objects.create(name="Pump checks", equipment_type=Equipment.EquipmentType.HYDRAULIC_PUMP)
        self.assertEqual(InspectionTemplate.objects.template_for(self.machine), pump)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PhotoTests(InspectionTestBase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def test_valid_photo_is_resized_renamed_and_stripped(self):
        exif = Image.Exif()
        exif[0x010F] = "SecretCam"  # camera make
        clean = services.process_photo(upload(content=image_bytes(exif=exif.tobytes())))
        image = Image.open(io.BytesIO(clean.read()))
        self.assertEqual(image.format, "JPEG")
        self.assertLessEqual(max(image.size), 1600)
        self.assertEqual(len(image.getexif()), 0)

    def test_png_and_webp_accepted(self):
        services.process_photo(upload("a.png", image_bytes("PNG", (50, 50)), "image/png"))
        services.process_photo(upload("a.webp", image_bytes("WEBP", (50, 50)), "image/webp"))

    def test_wrong_type_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Only JPEG, PNG or WebP"):
            services.process_photo(upload("a.gif", image_bytes("GIF", (50, 50)), "image/gif"))

    def test_oversize_rejected(self):
        big = SimpleUploadedFile("big.jpg", b"\xff\xd8" + b"0" * (5 * 1024 * 1024), content_type="image/jpeg")
        with self.assertRaisesMessage(ValidationError, "larger than 5 MB"):
            services.process_photo(big)

    def test_fake_jpg_rejected(self):
        fake = SimpleUploadedFile("evil.jpg", b"<?php echo 'not an image'; ?>", content_type="image/jpeg")
        with self.assertRaisesMessage(ValidationError, "not a valid image"):
            services.process_photo(fake)

    def test_upload_through_page_and_permission_checked_view(self):
        inspection = self.started()
        response_row = inspection.responses.first()
        self.client.force_login(self.inspector)
        payload = InspectionViewTests.perform_payload(self, inspection, ["PASS", "", ""])
        payload["form-0-photo_upload"] = upload()
        self.client.post(reverse("inspections:perform", args=[inspection.pk]), payload)
        response_row.refresh_from_db()
        self.assertRegex(response_row.photo.name, r"^inspection_photos/\d{4}/\d{2}/[0-9a-f]{32}\.jpg$")

        url = reverse("inspections:photo", args=[response_row.pk])
        photo = self.client.get(url)
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo["Content-Type"], "image/jpeg")
        photo.close()
        self.client.force_login(self.other_inspector)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_fake_photo_rejected_through_page(self):
        inspection = self.started()
        self.client.force_login(self.inspector)
        payload = InspectionViewTests.perform_payload(self, inspection, ["PASS", "", ""])
        payload["form-0-photo_upload"] = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
        response = self.client.post(reverse("inspections:perform", args=[inspection.pk]), payload)
        self.assertContains(response, "not a valid image")
        self.assertFalse(inspection.responses.first().photo)
