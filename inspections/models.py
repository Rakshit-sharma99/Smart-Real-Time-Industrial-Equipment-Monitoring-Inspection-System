import uuid
from collections import Counter

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.permissions import is_admin
from equipment.models import Equipment


class InspectionTemplateQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def template_for(self, equipment):
        """
        The checklist to use for a machine: an active template for its type,
        otherwise an active general template (no type). None if neither exists.
        """
        active = self.active()
        return (
            active.filter(equipment_type=equipment.equipment_type).order_by("name").first()
            or active.filter(equipment_type="").order_by("name").first()
        )


class InspectionTemplate(models.Model):
    name = models.CharField(max_length=120, unique=True)
    equipment_type = models.CharField(
        max_length=30,
        choices=Equipment.EquipmentType.choices,
        blank=True,
        help_text="Leave blank for a general checklist that fits any machine.",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InspectionTemplateQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ChecklistItem(models.Model):
    template = models.ForeignKey(InspectionTemplate, on_delete=models.CASCADE, related_name="items")
    order = models.PositiveSmallIntegerField(default=0)
    text = models.CharField(max_length=255)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text


class InspectionQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Object-level access: admins see every inspection, inspectors only their own."""
        if is_admin(user):
            return self.all()
        return self.filter(inspector=user)

    def open(self):
        return self.filter(status__in=Inspection.OPEN_STATUSES)


class Inspection(models.Model):
    class Status(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        SUBMITTED = "SUBMITTED", "Submitted"

    class Result(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        ATTENTION = "ATTENTION", "Needs attention"

    OPEN_STATUSES = (Status.ASSIGNED, Status.IN_PROGRESS)

    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="inspections")
    template = models.ForeignKey(InspectionTemplate, on_delete=models.PROTECT, related_name="inspections")
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inspections"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inspections_created"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ASSIGNED)
    due_date = models.DateField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    overall_result = models.CharField(max_length=10, choices=Result.choices, blank=True)
    remarks = models.TextField("Overall remarks", blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = InspectionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["inspector", "status"], name="inspection_inspector_status"),
            models.Index(fields=["equipment", "-created_at"], name="inspection_equipment_time"),
        ]

    def __str__(self):
        return f"{self.reference} {self.equipment.equipment_code}"

    @property
    def reference(self):
        return f"INS-{self.pk:05d}" if self.pk else "INS-new"

    @property
    def is_submitted(self):
        return self.status == self.Status.SUBMITTED

    @property
    def is_adhoc(self):
        return self.created_by_id == self.inspector_id

    @property
    def is_overdue(self):
        return bool(self.due_date and not self.is_submitted and self.due_date < timezone.localdate())

    def outcome_counts(self):
        """{"PASS": n, "FAIL": n, "NEEDS_ATTENTION": n, "NOT_APPLICABLE": n}."""
        counts = Counter(self.responses.values_list("outcome", flat=True))
        return {outcome: counts.get(outcome, 0) for outcome in InspectionResponse.Outcome.values}


def photo_upload_path(instance, filename):
    """inspection_photos/YYYY/MM/<uuid>.jpg - the uploaded file name is never used."""
    return f"inspection_photos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.jpg"


class InspectionResponse(models.Model):
    """One checklist line of one inspection. The item text is copied when the inspection starts."""

    class Outcome(models.TextChoices):
        PASS = "PASS", "Pass"
        FAIL = "FAIL", "Fail"
        NEEDS_ATTENTION = "NEEDS_ATTENTION", "Needs attention"
        NOT_APPLICABLE = "NOT_APPLICABLE", "N/A"

    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="responses")
    order = models.PositiveSmallIntegerField(default=0)
    item_text = models.CharField(max_length=255)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    remarks = models.TextField(blank=True)
    photo = models.ImageField(upload_to=photo_upload_path, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.inspection.reference} #{self.order}: {self.item_text}"


class DefectQuerySet(models.QuerySet):
    def visible_to(self, user):
        if is_admin(user):
            return self.all()
        return self.filter(inspection__inspector=user)

    def unresolved(self):
        """OPEN or IN_MAINTENANCE - a defect counts until its maintenance task is completed."""
        return self.exclude(status=Defect.Status.CLOSED)


class Defect(models.Model):
    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_MAINTENANCE = "IN_MAINTENANCE", "In maintenance"
        CLOSED = "CLOSED", "Closed"

    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="defects")
    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="defects")
    response = models.ForeignKey(
        InspectionResponse, on_delete=models.PROTECT, related_name="defects", null=True, blank=True
    )
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=Severity.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(default=timezone.now)

    objects = DefectQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["equipment", "status"], name="defect_equipment_status")]

    def __str__(self):
        return f"{self.reference} {self.equipment.equipment_code} ({self.severity})"

    @property
    def reference(self):
        return f"DF-{self.pk:05d}" if self.pk else "DF-new"
