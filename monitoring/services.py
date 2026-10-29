"""
Monitoring engine.

process_reading() is the single entry point for new sensor data. Inside one
database transaction it:
  1. validates and saves the SensorReading
  2. evaluates each metric against the machine's thresholds
  3. updates Equipment.health_status and last_reading_at
  4. creates / escalates / auto-resolves alerts (dedup + hysteresis)
After the transaction commits, the result is broadcast to open dashboards.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.permissions import is_admin
from equipment.metrics import CRITICAL, METRICS, NORMAL, classify, worst
from equipment.models import Equipment
from equipment.services import has_open_high_defect, health_from

from . import broadcast
from .models import Alert, SensorReading, SimulatorSettings, Source

# Hysteresis: to count as "recovered", a value must be at least 5 % below the
# warning limit, three readings in a row. This stops alerts flapping open/closed
# when a value hovers around the limit.
RECOVERY_MARGIN = Decimal("0.05")
RECOVERY_READINGS = 3
AUTO_RESOLVE_NOTE = "Auto-resolved: readings returned to normal"


class AlertActionError(Exception):
    """An alert action that is not allowed in the alert's current state."""


@dataclass
class ReadingResult:
    reading: SensorReading
    health_status: str
    alert_events: list = field(default_factory=list)  # [(event, alert), ...]


def process_reading(equipment, *, temperature_c, vibration_mm_s, pressure_bar, source=Source.SIMULATED):
    with transaction.atomic():
        equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
        if not equipment.is_active:
            raise ValidationError(f"{equipment.equipment_code} is deactivated and does not accept readings.")

        # 1. validate + save
        reading = SensorReading(
            equipment=equipment,
            temperature_c=temperature_c,
            vibration_mm_s=vibration_mm_s,
            pressure_bar=pressure_bar,
            source=source,
        )
        reading.full_clean()
        reading.save()

        # 2 + 4. evaluate each metric and manage its alert
        now = timezone.now()
        statuses = []
        alert_events = []
        for metric in METRICS:
            value = reading.value_for(metric)
            status = classify(value, *equipment.thresholds(metric))
            statuses.append(status)
            event = update_alert_for_metric(equipment, metric, value, status, source, now)
            if event:
                alert_events.append(event)

        # 3. health + last reading time
        equipment.last_reading_at = reading.recorded_at
        equipment.health_status = health_from(worst(statuses), has_open_high_defect(equipment))
        equipment.save(update_fields=["last_reading_at", "health_status", "updated_at"])

        transaction.on_commit(partial(broadcast.broadcast_reading, reading, equipment, alert_events))

    return ReadingResult(reading, equipment.health_status, alert_events)


def update_alert_for_metric(equipment, metric, value, status, source, now):
    """
    Apply the alert rules for one metric of one reading.
    Returns ("created" | "escalated" | "resolved", alert) or None when nothing notable happened.
    """
    warn, crit = equipment.thresholds(metric)
    alert = Alert.objects.open().filter(equipment=equipment, metric=metric.key).first()

    if status != NORMAL:
        if alert is None:
            # New breach -> new alert.
            threshold = crit if status == CRITICAL else warn
            alert = Alert.objects.create(
                equipment=equipment,
                metric=metric.key,
                severity=status,
                status=Alert.Status.ACTIVE,
                first_value=value,
                last_value=value,
                peak_value=value,
                threshold_value=threshold,
                description=describe_breach(equipment, metric, status, value, threshold, source),
                created_at=now,
                last_seen_at=now,
            )
            return ("created", alert)

        # Dedup: a breach while an alert is open updates that alert.
        alert.last_value = value
        alert.peak_value = max(alert.peak_value, value)
        alert.last_seen_at = now
        alert.normal_streak = 0
        escalated = status == CRITICAL and alert.severity == Alert.Severity.WARNING
        if escalated:
            # Escalate WARNING -> CRITICAL. Severity never goes back down automatically.
            alert.severity = Alert.Severity.CRITICAL
            alert.escalated_at = now
            alert.threshold_value = crit
            alert.description = describe_breach(equipment, metric, CRITICAL, value, crit, source, escalated=True)
        alert.save()
        return ("escalated", alert) if escalated else None

    if alert is None:
        return None

    # A normal reading while an alert is open: count towards recovery.
    alert.last_value = value
    if value < warn * (1 - RECOVERY_MARGIN):
        alert.normal_streak += 1
    else:
        alert.normal_streak = 0  # inside the hysteresis band: not recovered yet
    if alert.normal_streak >= RECOVERY_READINGS:
        alert.status = Alert.Status.RESOLVED
        alert.resolved_at = now
        alert.resolved_by = None
        alert.resolution_notes = AUTO_RESOLVE_NOTE
        alert.save()
        return ("resolved", alert)
    alert.save()
    return None


def describe_breach(equipment, metric, severity, value, threshold, source, escalated=False):
    text = (
        f"{metric.label} reached {value} {metric.unit} on {equipment.equipment_code} ({equipment.name}), "
        f"at or above the {severity.lower()} limit of {threshold} {metric.unit}."
    )
    if escalated:
        text = "Escalated to CRITICAL. " + text
    if source == Source.SIMULATED:
        text += " Source: SIMULATED sensor data (demo reading, not a real emergency)."
    return text


# --- Manual alert actions ----------------------------------------------------------


def acknowledge_alert(alert, user):
    """ACTIVE -> ACKNOWLEDGED. Any logged-in role may acknowledge."""
    with transaction.atomic():
        alert = Alert.objects.select_for_update().select_related("equipment").get(pk=alert.pk)
        if alert.status != Alert.Status.ACTIVE:
            raise AlertActionError("Only active alerts can be acknowledged.")
        alert.status = Alert.Status.ACKNOWLEDGED
        alert.acknowledged_by = user
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at"])
        transaction.on_commit(partial(broadcast.broadcast_alert, "acknowledged", alert))
    return alert


def resolve_alert(alert, user, notes):
    """ACTIVE/ACKNOWLEDGED -> RESOLVED. Admin only; resolution notes are required."""
    if not is_admin(user):
        raise PermissionDenied("Only admins can resolve alerts.")
    notes = (notes or "").strip()
    if not notes:
        raise ValidationError({"resolution_notes": "Resolution notes are required."})
    with transaction.atomic():
        alert = Alert.objects.select_for_update().select_related("equipment").get(pk=alert.pk)
        if alert.status == Alert.Status.RESOLVED:
            raise AlertActionError("This alert is already resolved.")
        alert.status = Alert.Status.RESOLVED
        alert.resolved_by = user
        alert.resolved_at = timezone.now()
        alert.resolution_notes = notes
        alert.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_notes"])
        transaction.on_commit(partial(broadcast.broadcast_alert, "resolved", alert))
    return alert


# --- Read helpers ----------------------------------------------------------------------


def latest_readings(equipment_list):
    """
    {equipment_pk: latest SensorReading} for several machines in ONE query.
    Works because process_reading() sets last_reading_at to the newest reading's time.
    """
    readings = SensorReading.objects.filter(
        equipment__in=equipment_list, recorded_at=F("equipment__last_reading_at")
    ).order_by("-id")
    latest = {}
    for reading in readings:
        latest.setdefault(reading.equipment_id, reading)
    return latest


def reading_values(reading):
    """Metric values keyed like the WebSocket payload: {"temperature": "49.04", ...}."""
    if reading is None:
        return None
    return {metric.key: str(reading.value_for(metric)) for metric in METRICS}


# --- Simulator bookkeeping -------------------------------------------------------------


def record_ingest():
    """Remember when the simulator last delivered data (shown on the Settings page)."""
    sim = SimulatorSettings.load()
    sim.last_ingest_at = timezone.now()
    sim.save(update_fields=["last_ingest_at", "updated_at"])
