from django.conf import settings
from django.db import models
from django.utils import timezone

from core.permissions import is_manager
from tables.models import Restaurant, RestaurantTable


class WaiterTaskQuerySet(models.QuerySet):
    def for_restaurant(self, restaurant):
        return self.filter(restaurant=restaurant)

    def visible_to(self, user, restaurant):
        """Managers see all tasks of their restaurant; waiters only the tasks assigned to them."""
        tasks = self.for_restaurant(restaurant)
        if is_manager(user):
            return tasks
        return tasks.filter(assigned_to=user)

    def open(self):
        return self.exclude(status=WaiterTask.Status.COMPLETED)

    def active(self):
        """Assigned or in progress - work a waiter is responsible for right now."""
        return self.filter(status__in=[WaiterTask.Status.ASSIGNED, WaiterTask.Status.IN_PROGRESS])

    def overdue(self, now=None):
        return self.open().filter(due_at__lt=now or timezone.now())


class WaiterTask(models.Model):
    class TaskType(models.TextChoices):
        CLEAR_DISHES = "CLEAR_DISHES", "Clear used dishes"
        REMOVE_LEFTOVERS = "REMOVE_LEFTOVERS", "Remove leftover food"
        CLEAN_TABLE = "CLEAN_TABLE", "Clean the table"
        ARRANGE_CHAIRS = "ARRANGE_CHAIRS", "Arrange chairs"
        PREPARE_TABLE = "PREPARE_TABLE", "Prepare the table"
        VERIFY_READINESS = "VERIFY_READINESS", "Verify table readiness"
        FULL_RESET = "FULL_RESET", "Full reset (clear, clean, arrange, prepare)"

    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        NORMAL = "NORMAL", "Normal"
        HIGH = "HIGH", "High"
        URGENT = "URGENT", "Urgent"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        ASSIGNED = "ASSIGNED", "Assigned"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"

    # Human-readable code (WT-0001), filled in from the database id after the first save.
    task_code = models.CharField(max_length=12, unique=True, null=True, blank=True, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="waiter_tasks")
    table = models.ForeignKey(RestaurantTable, on_delete=models.PROTECT, related_name="tasks")
    task_type = models.CharField("Task", max_length=20, choices=TaskType.choices, default=TaskType.FULL_RESET)
    description = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="waiter_tasks"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    due_at = models.DateTimeField("Deadline", null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_remarks = models.TextField(blank=True)
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    objects = WaiterTaskQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["assigned_to", "status"], name="task_assignee_status"),
            models.Index(fields=["restaurant", "status"], name="task_restaurant_status"),
            models.Index(fields=["table", "status"], name="task_table_status"),
        ]

    def __str__(self):
        return f"{self.task_code} {self.get_task_type_display()} {self.table}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.task_code:
            self.task_code = f"WT-{self.pk:04d}"
            WaiterTask.objects.filter(pk=self.pk).update(task_code=self.task_code)

    @property
    def is_overdue(self):
        return bool(self.due_at and self.status != self.Status.COMPLETED and self.due_at < timezone.now())

    @property
    def duration_minutes(self):
        """Minutes from start to completion (None until both are known)."""
        if self.started_at and self.completed_at:
            return round((self.completed_at - self.started_at).total_seconds() / 60, 1)
        return None


class WaiterTaskHistory(models.Model):
    """Every action on a task: who, what, when, from which status to which."""

    class Action(models.TextChoices):
        CREATED = "CREATED", "Created"
        ASSIGNED = "ASSIGNED", "Assigned"
        UNASSIGNED = "UNASSIGNED", "Unassigned"
        REASSIGNED = "REASSIGNED", "Reassigned"
        ACCEPTED = "ACCEPTED", "Accepted"
        STARTED = "STARTED", "Started"
        PROGRESS = "PROGRESS", "Progress update"
        PRIORITY = "PRIORITY", "Priority changed"
        COMPLETED = "COMPLETED", "Completed"

    task = models.ForeignKey(WaiterTask, on_delete=models.CASCADE, related_name="history")
    action = models.CharField(max_length=12, choices=Action.choices)
    from_status = models.CharField(max_length=15, choices=WaiterTask.Status.choices, blank=True)
    to_status = models.CharField(max_length=15, choices=WaiterTask.Status.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "waiter task history"

    def __str__(self):
        return f"{self.task.task_code}: {self.get_action_display()}"
