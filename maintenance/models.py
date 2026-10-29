from django.conf import settings
from django.db import models
from django.utils import timezone

from core.permissions import is_admin
from equipment.models import Equipment
from inspections.models import Defect
from monitoring.models import Alert


class MaintenanceTaskQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Admins see every task; inspectors see the tasks assigned to them."""
        if is_admin(user):
            return self.all()
        return self.filter(assigned_to=user)

    def open(self):
        return self.exclude(status=MaintenanceTask.Status.COMPLETED)


class MaintenanceTask(models.Model):
    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"

    # Human-readable code (MT-0001), filled in from the database id after the first save.
    task_code = models.CharField(max_length=12, unique=True, null=True, blank=True, editable=False)
    equipment = models.ForeignKey(Equipment, on_delete=models.PROTECT, related_name="tasks")
    alert = models.ForeignKey(Alert, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    defect = models.ForeignKey(Defect, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="maintenance_tasks"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="maintenance_tasks_created"
    )
    due_date = models.DateField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    objects = MaintenanceTaskQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["assigned_to", "status"], name="task_assignee_status")]

    def __str__(self):
        return f"{self.task_code} {self.title}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.task_code:
            self.task_code = f"MT-{self.pk:04d}"
            MaintenanceTask.objects.filter(pk=self.pk).update(task_code=self.task_code)

    @property
    def is_overdue(self):
        return bool(self.due_date and self.status != self.Status.COMPLETED and self.due_date < timezone.localdate())


class TaskStatusChange(models.Model):
    """History row written for every status change (who, from, to, when, note)."""

    task = models.ForeignKey(MaintenanceTask, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=15, choices=MaintenanceTask.Status.choices, blank=True)
    to_status = models.CharField(max_length=15, choices=MaintenanceTask.Status.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    note = models.TextField(blank=True)
    changed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["changed_at", "id"]

    def __str__(self):
        return f"{self.task.task_code}: {self.from_status or '-'} -> {self.to_status}"
