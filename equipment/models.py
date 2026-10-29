import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Count, F, Q
from django.urls import reverse
from django.utils import timezone

from .metrics import METRICS


def offline_cutoff():
    """Readings older than this mean the machine is OFFLINE."""
    return timezone.now() - timedelta(seconds=settings.OFFLINE_AFTER_SECONDS)


class EquipmentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def offline(self):
        """Active machines with no reading, or no reading recently."""
        return self.active().filter(
            Q(last_reading_at__isnull=True) | Q(last_reading_at__lt=offline_cutoff())
        )

    def online(self):
        return self.active().filter(last_reading_at__gte=offline_cutoff())

    def with_display_status(self, status):
        """Filter by the status shown in the UI (includes OFFLINE and INACTIVE)."""
        if status == Equipment.INACTIVE:
            return self.filter(is_active=False)
        if status == Equipment.OFFLINE:
            return self.offline()
        return self.online().filter(health_status=status)

    def status_counts(self):
        """Counts for the dashboard in one query. Inactive machines are excluded from 'total'."""
        cutoff = offline_cutoff()
        online = Q(is_active=True, last_reading_at__gte=cutoff)
        return self.aggregate(
            total=Count("id", filter=Q(is_active=True)),
            healthy=Count("id", filter=online & Q(health_status=Equipment.Health.HEALTHY)),
            warning=Count("id", filter=online & Q(health_status=Equipment.Health.WARNING)),
            critical=Count("id", filter=online & Q(health_status=Equipment.Health.CRITICAL)),
            offline=Count(
                "id",
                filter=Q(is_active=True) & (Q(last_reading_at__isnull=True) | Q(last_reading_at__lt=cutoff)),
            ),
            inactive=Count("id", filter=Q(is_active=False)),
        )


def threshold_field(verbose_name, default):
    return models.DecimalField(
        verbose_name,
        max_digits=7,
        decimal_places=2,
        default=Decimal(default),
        validators=[MinValueValidator(Decimal("0.01"))],
    )


class Equipment(models.Model):
    class EquipmentType(models.TextChoices):
        AIR_COMPRESSOR = "AIR_COMPRESSOR", "Air compressor"
        HYDRAULIC_PUMP = "HYDRAULIC_PUMP", "Hydraulic pump"
        CNC_MACHINE = "CNC_MACHINE", "CNC machine"
        BOILER = "BOILER", "Boiler"
        CONVEYOR_MOTOR = "CONVEYOR_MOTOR", "Conveyor motor"
        COOLING_TOWER = "COOLING_TOWER", "Cooling tower"
        OTHER = "OTHER", "Other"

    class Health(models.TextChoices):
        HEALTHY = "HEALTHY", "Healthy"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    class SimMode(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        DRIFT = "DRIFT", "Drift (rises slowly, then resets)"
        FORCE_WARNING = "FORCE_WARNING", "Force warning"
        FORCE_CRITICAL = "FORCE_CRITICAL", "Force critical"
        OFFLINE = "OFFLINE", "Offline (send nothing)"

    # Display-only statuses, computed at read time (never stored in health_status).
    OFFLINE = "OFFLINE"
    INACTIVE = "INACTIVE"
    DISPLAY_STATUS_CHOICES = [
        ("HEALTHY", "Healthy"),
        ("WARNING", "Warning"),
        ("CRITICAL", "Critical"),
        (OFFLINE, "Offline"),
        (INACTIVE, "Inactive"),
    ]

    equipment_code = models.CharField(
        "Equipment code", max_length=20, unique=True, help_text="Short code for people, e.g. EQ-007."
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120)
    equipment_type = models.CharField("Type", max_length=30, choices=EquipmentType.choices)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=120)
    installed_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    health_status = models.CharField(max_length=10, choices=Health.choices, default=Health.HEALTHY)
    last_reading_at = models.DateTimeField(null=True, blank=True)

    temp_warn = threshold_field("Temperature warning (°C)", "70.00")
    temp_crit = threshold_field("Temperature critical (°C)", "85.00")
    vib_warn = threshold_field("Vibration warning (mm/s)", "7.10")
    vib_crit = threshold_field("Vibration critical (mm/s)", "11.00")
    pres_warn = threshold_field("Pressure warning (bar)", "8.00")
    pres_crit = threshold_field("Pressure critical (bar)", "10.00")

    sim_mode = models.CharField(
        "Simulator mode", max_length=20, choices=SimMode.choices, default=SimMode.NORMAL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EquipmentQuerySet.as_manager()

    class Meta:
        ordering = ["equipment_code"]
        verbose_name_plural = "equipment"
        constraints = [
            models.CheckConstraint(
                condition=Q(temp_warn__gt=0) & Q(temp_crit__gt=F("temp_warn")),
                name="equipment_temp_thresholds_valid",
            ),
            models.CheckConstraint(
                condition=Q(vib_warn__gt=0) & Q(vib_crit__gt=F("vib_warn")),
                name="equipment_vib_thresholds_valid",
            ),
            models.CheckConstraint(
                condition=Q(pres_warn__gt=0) & Q(pres_crit__gt=F("pres_warn")),
                name="equipment_pres_thresholds_valid",
            ),
        ]

    def __str__(self):
        return f"{self.equipment_code} · {self.name}"

    def clean(self):
        """Every metric needs 0 < warning < critical."""
        errors = {}
        for metric in METRICS:
            warn = getattr(self, metric.warn_field)
            crit = getattr(self, metric.crit_field)
            if warn is None or crit is None:
                continue  # the field itself reports "required"
            if warn <= 0:
                errors[metric.warn_field] = "Must be greater than 0."
            if crit <= warn:
                errors[metric.crit_field] = (
                    f"{metric.label} critical limit must be greater than the warning limit ({warn})."
                )
        if errors:
            raise ValidationError(errors)

    def get_absolute_url(self):
        return reverse("equipment:detail", args=[self.equipment_code])

    @property
    def qr_target_url(self):
        """What the QR code encodes. Opening it requires login; it grants nothing by itself."""
        return f"{settings.SITE_BASE_URL}{reverse('equipment_qr_redirect', args=[self.public_id])}"

    @property
    def is_offline(self):
        return self.is_active and (
            self.last_reading_at is None or self.last_reading_at < offline_cutoff()
        )

    @property
    def display_status(self):
        if not self.is_active:
            return self.INACTIVE
        if self.is_offline:
            return self.OFFLINE
        return self.health_status

    @property
    def display_status_label(self):
        return dict(self.DISPLAY_STATUS_CHOICES)[self.display_status]

    def thresholds(self, metric):
        """(warning, critical) for one metric."""
        return getattr(self, metric.warn_field), getattr(self, metric.crit_field)

    def threshold_rows(self):
        """[(metric, warning, critical), ...] for templates and reports."""
        return [(metric, *self.thresholds(metric)) for metric in METRICS]
