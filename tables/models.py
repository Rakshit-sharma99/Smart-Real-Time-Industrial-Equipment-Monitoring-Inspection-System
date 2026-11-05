import uuid
from datetime import date, datetime, time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Restaurant(models.Model):
    """One restaurant. Every table, booking, task and staff member belongs to exactly one."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    address = models.CharField(max_length=200, blank=True)
    opens_at = models.TimeField("Opens at", default=time(11, 0))
    closes_at = models.TimeField("Closes at", default=time(23, 0))
    reservation_minutes = models.PositiveSmallIntegerField(
        "Default sitting length (minutes)", default=90, validators=[MinValueValidator(15), MaxValueValidator(360)]
    )
    reservation_hold_minutes = models.PositiveSmallIntegerField(
        "Hold a booked table for (minutes before arrival)",
        default=30,
        validators=[MaxValueValidator(180)],
        help_text="A table shows as Reserved from this long before the booking starts.",
    )
    cleaning_deadline_minutes = models.PositiveSmallIntegerField(
        "Default cleaning deadline (minutes)", default=15, validators=[MinValueValidator(1), MaxValueValidator(240)]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def open_hours_per_day(self):
        """Opening hours per day (handles closing after midnight)."""
        opened = datetime.combine(date.min, self.opens_at)
        closed = datetime.combine(date.min, self.closes_at)
        hours = (closed - opened).total_seconds() / 3600
        return hours if hours > 0 else hours + 24


class RestaurantSection(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=60)
    floor = models.CharField(max_length=60, blank=True, help_text="e.g. Ground floor, Terrace")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [models.UniqueConstraint(fields=["restaurant", "name"], name="unique_section_name_per_restaurant")]

    def __str__(self):
        return self.name


class TableStatus(models.TextChoices):
    """What staff see. Computed from the stored facts - never stored on the table itself."""

    READY = "READY", "Ready"
    OCCUPIED = "OCCUPIED", "Occupied"
    RESERVED = "RESERVED", "Reserved"
    VACANT_NEEDS_CLEARING = "VACANT_NEEDS_CLEARING", "Vacant – needs clearing"
    CLEANING_REQUIRED = "CLEANING_REQUIRED", "Cleaning required"
    CLEANING_IN_PROGRESS = "CLEANING_IN_PROGRESS", "Cleaning in progress"
    BLOCKED = "BLOCKED", "Blocked"
    INACTIVE = "INACTIVE", "Inactive"


NEEDS_CLEANING_STATUSES = (TableStatus.VACANT_NEEDS_CLEARING, TableStatus.CLEANING_REQUIRED)


class Cleanliness(models.TextChoices):
    CLEAN = "CLEAN", "Clean"
    NEEDS_CLEARING = "NEEDS_CLEARING", "Dishes or glasses on the table"
    NEEDS_CLEANING = "NEEDS_CLEANING", "Needs cleaning"


def compute_status(*, is_active, is_blocked, is_occupied, cleanliness, cleaning_in_progress, reservation_hold):
    """
    The single rule for a table's displayed status, checked in priority order.
    A vacant table with dishes on it is never Ready: cleaning must finish first.
    """
    if not is_active:
        return TableStatus.INACTIVE
    if is_blocked:
        return TableStatus.BLOCKED
    if is_occupied:
        return TableStatus.OCCUPIED
    if cleaning_in_progress:
        return TableStatus.CLEANING_IN_PROGRESS
    if cleanliness == Cleanliness.NEEDS_CLEARING:
        return TableStatus.VACANT_NEEDS_CLEARING
    if cleanliness == Cleanliness.NEEDS_CLEANING:
        return TableStatus.CLEANING_REQUIRED
    if reservation_hold:
        return TableStatus.RESERVED
    return TableStatus.READY


class RestaurantTable(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="tables")
    section = models.ForeignKey(RestaurantSection, on_delete=models.PROTECT, related_name="tables")
    number = models.PositiveSmallIntegerField(help_text="Shown as T<number>, e.g. 12 -> T12.")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    capacity = models.PositiveSmallIntegerField(
        "Seats", validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    is_active = models.BooleanField("Active", default=True)

    # Stored facts. Occupancy and cleanliness are separate on purpose:
    # a table can be empty but still have dirty dishes on it.
    is_occupied = models.BooleanField(default=False)
    cleanliness = models.CharField(max_length=20, choices=Cleanliness.choices, default=Cleanliness.CLEAN)
    is_blocked = models.BooleanField(default=False)
    block_reason = models.CharField(max_length=160, blank=True)

    assigned_waiter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tables"
    )
    last_inspected_at = models.DateTimeField(null=True, blank=True)
    status_updated_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["restaurant", "number"], name="unique_table_number_per_restaurant")]
        indexes = [models.Index(fields=["restaurant", "is_active"], name="table_restaurant_active")]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f"T{self.number}"

    def clean(self):
        if self.section_id and self.restaurant_id and self.section.restaurant_id != self.restaurant_id:
            raise ValidationError({"section": "This section belongs to another restaurant."})

    def get_absolute_url(self):
        return reverse("tables:detail", args=[self.pk])

    @property
    def qr_target_url(self):
        """What the table's QR code encodes. Opening it still requires a staff login."""
        return f"{settings.SITE_BASE_URL}{reverse('table_qr_redirect', args=[self.public_id])}"


class TableStatusHistory(models.Model):
    """Audit log: every change of a table's condition, status, block or assigned waiter."""

    class Source(models.TextChoices):
        INSPECTION = "INSPECTION", "Staff inspection"
        CAMERA = "CAMERA", "Camera detection"
        TASK = "TASK", "Waiter task"
        RESERVATION = "RESERVATION", "Reservation"
        OVERRIDE = "OVERRIDE", "Manager override"
        BLOCK = "BLOCK", "Block / unblock"
        ASSIGNMENT = "ASSIGNMENT", "Waiter assignment"
        SETUP = "SETUP", "Table setup"

    table = models.ForeignKey(RestaurantTable, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=25, choices=TableStatus.choices, blank=True)
    to_status = models.CharField(max_length=25, choices=TableStatus.choices)
    is_occupied = models.BooleanField()
    cleanliness = models.CharField(max_length=20, choices=Cleanliness.choices)
    is_blocked = models.BooleanField()
    source = models.CharField(max_length=15, choices=Source.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "table status history"
        indexes = [models.Index(fields=["table", "-created_at"], name="table_history_time")]

    def __str__(self):
        return f"{self.table} {self.from_status or '-'} -> {self.to_status}"
