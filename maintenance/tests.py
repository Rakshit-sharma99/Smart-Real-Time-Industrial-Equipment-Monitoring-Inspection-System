from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from core.testutils import make_admin, make_inspector
from equipment.models import Equipment
from inspections import services as inspection_services
from inspections.models import Defect, InspectionResponse, InspectionTemplate
from monitoring.models import Alert
from monitoring.services import process_reading

from . import services
from .models import MaintenanceTask, TaskStatusChange

Status = MaintenanceTask.Status


class TaskTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_admin()
        cls.inspector = make_inspector("ivan")
        cls.other = make_inspector("isha")
        cls.machine = Equipment.objects.create(
            equipment_code="EQ-001", name="Boiler", equipment_type=Equipment.EquipmentType.BOILER, location="Bay"
        )

    def new_task(self, assignee=None, **extra):
        fields = {"title": "Fix it", "description": "", "priority": "MEDIUM", "due_date": None}
        fields.update(extra)
        return services.create_task(equipment=self.machine, created_by=self.admin, assignee=assignee, **fields)

    def reading(self, temperature):
        process_reading(self.machine, temperature_c=Decimal(temperature), vibration_mm_s=Decimal("3"), pressure_bar=Decimal("4"))

    def high_defect(self):
        """Submit an inspection with one FAIL so the machine gets a HIGH defect."""
        template = InspectionTemplate.objects.create(name="T")
        template.items.create(order=1, text="No leaks")
        inspection = inspection_services.assign_inspection(
            equipment=self.machine, template=template, inspector=self.inspector, due_date=None, created_by=self.admin
        )
        inspection_services.start_inspection(inspection, self.inspector)
        inspection.responses.update(outcome=InspectionResponse.Outcome.FAIL, remarks="Leaking")
        inspection_services.submit_inspection(inspection, self.inspector)
        return Defect.objects.get()


class TaskCreationTests(TaskTestBase):
    def test_task_code_and_history(self):
        task = self.new_task()
        self.assertRegex(task.task_code, r"^MT-\d{4}$")
        self.assertEqual(task.task_code, f"MT-{task.pk:04d}")
        self.assertEqual(task.status, Status.OPEN)
        self.assertEqual(task.history.get().note, "Task created")

    def test_create_with_assignee_goes_to_assigned(self):
        task = self.new_task(assignee=self.inspector)
        self.assertEqual(task.status, Status.ASSIGNED)
        self.assertEqual(list(task.history.values_list("to_status", flat=True)), [Status.OPEN, Status.ASSIGNED])

    def test_only_admin_creates(self):
        with self.assertRaises(PermissionDenied):
            services.create_task(equipment=self.machine, created_by=self.inspector, title="x", description="",
                                 priority="LOW", due_date=None)

    def test_convert_defect_moves_it_to_maintenance_once(self):
        defect = self.high_defect()
        task = self.new_task(defect=defect)
        defect.refresh_from_db()
        self.assertEqual(defect.status, Defect.Status.IN_MAINTENANCE)
        self.assertEqual(task.defect, defect)
        with self.assertRaises(services.TransitionError):
            self.new_task(defect=defect)

    def test_convert_alert_links_but_does_not_change_alert(self):
        self.reading("90")
        alert = Alert.objects.get()
        task = self.new_task(alert=alert)
        services.change_status(task, Status.ASSIGNED, self.admin, assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.inspector)
        services.change_status(task, Status.COMPLETED, self.inspector, resolution_notes="Fixed")
        alert.refresh_from_db()
        self.assertEqual(alert.status, Alert.Status.ACTIVE)  # alerts are never auto-resolved by tasks


class TransitionTests(TaskTestBase):
    def test_valid_path_by_the_right_users(self):
        task = self.new_task()
        services.change_status(task, Status.ASSIGNED, self.admin, assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.inspector)
        task = services.change_status(task, Status.COMPLETED, self.inspector, resolution_notes="Replaced seal")
        self.assertEqual(task.status, Status.COMPLETED)
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.history.count(), 4)
        last = task.history.last()
        self.assertEqual((last.from_status, last.to_status, last.changed_by), (Status.IN_PROGRESS, Status.COMPLETED, self.inspector))

    def test_assign_requires_assignee(self):
        with self.assertRaises(ValidationError):
            services.change_status(self.new_task(), Status.ASSIGNED, self.admin)

    def test_inspector_cannot_assign_or_unassign(self):
        task = self.new_task()
        with self.assertRaises(PermissionDenied):
            services.change_status(task, Status.ASSIGNED, self.inspector, assignee=self.inspector)
        services.change_status(task, Status.ASSIGNED, self.admin, assignee=self.inspector)
        with self.assertRaises(PermissionDenied):
            services.change_status(task, Status.OPEN, self.inspector)

    def test_unassign_clears_assignee(self):
        task = self.new_task(assignee=self.inspector)
        task = services.change_status(task, Status.OPEN, self.admin)
        self.assertIsNone(task.assigned_to)

    def test_other_inspector_cannot_start(self):
        task = self.new_task(assignee=self.inspector)
        with self.assertRaises(PermissionDenied):
            services.change_status(task, Status.IN_PROGRESS, self.other)

    def test_admin_may_do_any_valid_transition(self):
        task = self.new_task(assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.admin)
        task = services.change_status(task, Status.COMPLETED, self.admin, resolution_notes="Done by admin")
        self.assertEqual(task.status, Status.COMPLETED)

    def test_complete_requires_notes(self):
        task = self.new_task(assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.inspector)
        with self.assertRaises(ValidationError):
            services.change_status(task, Status.COMPLETED, self.inspector, resolution_notes="  ")

    def test_invalid_transitions(self):
        task = self.new_task()
        for target in [Status.IN_PROGRESS, Status.COMPLETED]:
            with self.subTest(target=target), self.assertRaises(services.TransitionError):
                services.change_status(task, target, self.admin)

    def test_completed_is_terminal(self):
        task = self.new_task(assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.inspector)
        services.change_status(task, Status.COMPLETED, self.inspector, resolution_notes="ok")
        for target in [Status.OPEN, Status.ASSIGNED, Status.IN_PROGRESS]:
            with self.subTest(target=target), self.assertRaises(services.TransitionError):
                services.change_status(task, target, self.admin, assignee=self.inspector)

    def test_allowed_transitions_for_buttons(self):
        task = self.new_task(assignee=self.inspector)
        self.assertEqual(services.allowed_transitions(task, self.inspector), [Status.IN_PROGRESS])
        self.assertEqual(set(services.allowed_transitions(task, self.admin)), {Status.OPEN, Status.IN_PROGRESS})
        self.assertEqual(services.allowed_transitions(task, self.other), [])


class HealthAfterMaintenanceTests(TaskTestBase):
    def complete(self, task):
        services.change_status(task, Status.ASSIGNED, self.admin, assignee=self.inspector)
        services.change_status(task, Status.IN_PROGRESS, self.inspector)
        services.change_status(task, Status.COMPLETED, self.inspector, resolution_notes="Fixed")

    def test_completing_never_forces_healthy(self):
        self.reading("90")  # critical temperature
        defect = self.high_defect()
        self.complete(self.new_task(defect=defect))
        defect.refresh_from_db()
        self.machine.refresh_from_db()
        self.assertEqual(defect.status, Defect.Status.CLOSED)
        self.assertEqual(self.machine.health_status, Equipment.Health.CRITICAL)

    def test_health_recomputed_when_readings_are_normal(self):
        self.reading("40")
        defect = self.high_defect()
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.WARNING)  # HIGH defect
        task = self.new_task(defect=defect)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.WARNING)  # still unresolved
        self.complete(task)
        self.machine.refresh_from_db()
        self.assertEqual(self.machine.health_status, Equipment.Health.HEALTHY)


class TaskViewTests(TaskTestBase):
    def test_inspector_sees_only_own_tasks(self):
        mine = self.new_task(assignee=self.inspector)
        theirs = self.new_task(assignee=self.other)
        unassigned = self.new_task()
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("maintenance:list"))
        self.assertContains(response, mine.task_code)
        self.assertNotContains(response, theirs.task_code)
        self.assertNotContains(response, unassigned.task_code)
        self.assertEqual(self.client.get(reverse("maintenance:detail", args=[theirs.pk])).status_code, 404)

    def test_convert_defect_through_page(self):
        defect = self.high_defect()
        self.client.force_login(self.admin)
        url = reverse("maintenance:create") + f"?defect={defect.pk}"
        page = self.client.get(url)
        self.assertContains(page, defect.reference)
        response = self.client.post(url, {
            "title": "Fix leak", "description": "seal", "priority": "HIGH", "due_date": "", "assignee": self.inspector.pk,
        })
        task = MaintenanceTask.objects.get()
        self.assertRedirects(response, reverse("maintenance:detail", args=[task.pk]))
        self.assertEqual((task.defect, task.equipment, task.status), (defect, self.machine, Status.ASSIGNED))

    def test_inspector_cannot_create(self):
        self.client.force_login(self.inspector)
        self.assertEqual(self.client.get(reverse("maintenance:create")).status_code, 403)

    def test_assignee_moves_task_through_page(self):
        task = self.new_task(assignee=self.inspector)
        self.client.force_login(self.inspector)
        url = reverse("maintenance:change_status", args=[task.pk])
        self.client.post(url, {"to_status": "IN_PROGRESS"})
        response = self.client.post(url, {"to_status": "COMPLETED", "resolution_notes": ""})
        self.assertEqual(response.status_code, 400)
        self.client.post(url, {"to_status": "COMPLETED", "resolution_notes": "All good"})
        task.refresh_from_db()
        self.assertEqual(task.status, Status.COMPLETED)

    def test_inspector_cannot_assign_through_page(self):
        task = self.new_task()
        self.client.force_login(self.inspector)
        # Not visible to the inspector at all (unassigned) -> 404.
        response = self.client.post(reverse("maintenance:change_status", args=[task.pk]),
                                    {"to_status": "ASSIGNED", "assignee": self.inspector.pk})
        self.assertEqual(response.status_code, 404)
        task = self.new_task(assignee=self.inspector)
        response = self.client.post(reverse("maintenance:change_status", args=[task.pk]), {"to_status": "OPEN"})
        self.assertEqual(response.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, Status.ASSIGNED)

    def test_dashboard_shows_task_summary(self):
        self.new_task(assignee=self.inspector)
        self.client.force_login(self.inspector)
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'data-kpi="open_tasks"')
        self.assertEqual(response.context["summary"]["open_tasks"], 1)
        self.assertEqual(TaskStatusChange.objects.count(), 2)
