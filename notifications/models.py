from django.conf import settings
from django.db import models
from django.utils import timezone

from tables.models import Restaurant


class Notification(models.Model):
    class Kind(models.TextChoices):
        INSPECTION = "INSPECTION", "Inspection completed"
        CLEANING_NEEDED = "CLEANING_NEEDED", "Table needs cleaning"
        TASK_ASSIGNED = "TASK_ASSIGNED", "Task assigned"
        TASK_REASSIGNED = "TASK_REASSIGNED", "Task reassigned"
        TASK_PRIORITY = "TASK_PRIORITY", "Task priority changed"
        TASK_COMPLETED = "TASK_COMPLETED", "Task completed"
        TASK_OVERDUE = "TASK_OVERDUE", "Task overdue"
        RESERVATION_CREATED = "RESERVATION_CREATED", "Reservation created"
        RESERVATION_CANCELLED = "RESERVATION_CANCELLED", "Reservation cancelled"
        RESERVATION_CONFIRMED = "RESERVATION_CONFIRMED", "Reservation confirmed"
        TABLE_READY = "TABLE_READY", "Table ready"
        BOOKING_AFFECTED = "BOOKING_AFFECTED", "Booking affected by a table change"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=25, choices=Kind.choices)
    title = models.CharField(max_length=160)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=200, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["recipient", "is_read", "-created_at"], name="notification_inbox")]

    def __str__(self):
        return f"{self.recipient} - {self.title}"
