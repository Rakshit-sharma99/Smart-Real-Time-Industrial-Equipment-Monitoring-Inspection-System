import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.permissions import is_manager
from tables.models import RestaurantTable, TableStatus


class TableInspectionQuerySet(models.QuerySet):
    def for_restaurant(self, restaurant):
        return self.filter(table__restaurant=restaurant)

    def visible_to(self, user, restaurant):
        """Managers see every inspection of their restaurant; other staff see their own."""
        inspections = self.for_restaurant(restaurant)
        if is_manager(user):
            return inspections
        return inspections.filter(inspector=user)


class TableInspection(models.Model):
    """
    One look at a table: who is sitting there and what is on it.

    Sources: a staff member (usually after scanning the table's QR code), the
    check a waiter fills in when completing a task, or an external camera /
    computer-vision service posting to the detection API.
    """

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Staff inspection"
        TASK = "TASK", "Task completion check"
        CAMERA = "CAMERA", "Camera detection (external service)"

    table = models.ForeignKey(RestaurantTable, on_delete=models.PROTECT, related_name="inspections")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="table_inspections"
    )
    task = models.ForeignKey(
        "tasks.WaiterTask", on_delete=models.SET_NULL, null=True, blank=True, related_name="inspections"
    )
    device_id = models.CharField(max_length=60, blank=True, help_text="Camera / device that sent a detection.")
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    applied = models.BooleanField(default=True, help_text="False when a low-confidence detection was only recorded.")
    dishes_checked = models.BooleanField(default=True, help_text="False when the source could not look for dishes.")

    customers_present = models.BooleanField("Customers seated")
    plates_present = models.BooleanField("Plates on the table")
    glasses_present = models.BooleanField("Glasses on the table")
    leftovers_present = models.BooleanField("Leftover food")
    # A camera cannot judge these two, so they are optional (null = not checked).
    table_clean = models.BooleanField("Table surface clean", null=True, blank=True)
    chairs_arranged = models.BooleanField("Chairs arranged", null=True, blank=True)
    ready_for_next = models.BooleanField("Ready for the next customers", default=False)
    remarks = models.TextField(blank=True)

    resulting_status = models.CharField(max_length=25, choices=TableStatus.choices, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = TableInspectionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["table", "-created_at"], name="inspection_table_time"),
            models.Index(fields=["inspector", "-created_at"], name="inspection_inspector_time"),
        ]

    def __str__(self):
        return f"{self.reference} {self.table}"

    @property
    def reference(self):
        return f"IN-{self.pk:05d}" if self.pk else "IN-new"

    @property
    def dishes_present(self):
        return self.plates_present or self.glasses_present or self.leftovers_present


def photo_upload_path(instance, filename):
    """inspection_photos/YYYY/MM/<uuid>.jpg - the uploaded file name is never used."""
    return f"inspection_photos/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.jpg"


class TableInspectionPhoto(models.Model):
    inspection = models.ForeignKey(TableInspection, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=photo_upload_path)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Photo {self.pk} of {self.inspection.reference}"
