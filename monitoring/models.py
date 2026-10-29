from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from equipment.metrics import METRIC_CHOICES, METRICS_BY_KEY
from equipment.models import Equipment


class Source(models.TextChoices):
    SIMULATED = "SIMULATED", "Simulated"
    DEVICE = "DEVICE", "Device"


def measurement_field(verbose_name):
    return models.DecimalField(
        verbose_name, max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )


class SensorReading(models.Model):
    """One tick of data for one machine: all three metrics in one row."""

    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="readings")
    recorded_at = models.DateTimeField(default=timezone.now)
    temperature_c = measurement_field("Temperature (°C)")
    vibration_mm_s = measurement_field("Vibration (mm/s)")
    pressure_bar = measurement_field("Pressure (bar)")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.SIMULATED)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [models.Index(fields=["equipment", "-recorded_at"], name="reading_equipment_time_idx")]

    def __str__(self):
        return f"{self.equipment.equipment_code} @ {self.recorded_at:%Y-%m-%d %H:%M:%S}"

    def value_for(self, metric):
        return getattr(self, metric.reading_field)


class AlertQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status__in=Alert.OPEN_STATUSES)


class Alert(models.Model):
    class Severity(models.TextChoices):
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged"
        RESOLVED = "RESOLVED", "Resolved"

    OPEN_STATUSES = (Status.ACTIVE, Status.ACKNOWLEDGED)

    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="alerts")
    metric = models.CharField(max_length=20, choices=METRIC_CHOICES)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    first_value = models.DecimalField(max_digits=7, decimal_places=2)
    last_value = models.DecimalField(max_digits=7, decimal_places=2)
    peak_value = models.DecimalField(max_digits=7, decimal_places=2)
    threshold_value = models.DecimalField(
        max_digits=7, decimal_places=2, help_text="The limit that was crossed (warning or critical)."
    )
    description = models.TextField()
    normal_streak = models.PositiveSmallIntegerField(
        default=0, help_text="Consecutive readings safely below the warning limit."
    )
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    escalated_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True)

    objects = AlertQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "severity"], name="alert_status_severity_idx"),
            models.Index(fields=["equipment", "status"], name="alert_equipment_status_idx"),
        ]
        constraints = [
            # Dedup rule: at most one OPEN alert per machine and metric.
            models.UniqueConstraint(
                fields=["equipment", "metric"],
                condition=Q(status__in=["ACTIVE", "ACKNOWLEDGED"]),
                name="one_open_alert_per_equipment_metric",
            )
        ]

    def __str__(self):
        return f"{self.reference} {self.equipment.equipment_code} {self.metric} {self.severity}"

    @property
    def reference(self):
        return f"AL-{self.pk:05d}" if self.pk else "AL-new"

    @property
    def metric_info(self):
        return METRICS_BY_KEY[self.metric]

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class SimulatorSettings(models.Model):
    """Singleton (always pk=1): global simulator options, edited on the Settings page."""

    interval_seconds = models.PositiveSmallIntegerField(
        "Interval (seconds)",
        default=5,
        validators=[MinValueValidator(2), MaxValueValidator(60)],
        help_text="How often the simulator sends a batch of readings (2-60).",
    )
    noise_pct = models.DecimalField(
        "Noise (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("2.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("20"))],
        help_text="Random jitter applied to normal and drifting values (0-20 %).",
    )
    last_ingest_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "simulator settings"
        verbose_name_plural = "simulator settings"

    def __str__(self):
        return "Simulator settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        settings_row, _ = cls.objects.get_or_create(pk=1)
        return settings_row
