"""Equipment business logic: health, QR images and activation."""
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.image.pil import PilImage

from .metrics import CRITICAL, METRICS, WARNING, classify, worst
from .models import Equipment


def health_from(worst_metric_status, has_open_high_defect):
    """
    Map the worst metric status to a health status.
    An open HIGH-severity defect raises HEALTHY to WARNING (never lowers anything).
    """
    if worst_metric_status == CRITICAL:
        return Equipment.Health.CRITICAL
    if worst_metric_status == WARNING:
        return Equipment.Health.WARNING
    if has_open_high_defect:
        return Equipment.Health.WARNING
    return Equipment.Health.HEALTHY


def has_open_high_defect(equipment):
    """True when the machine has an OPEN defect of HIGH severity."""
    return False  # Defects arrive with the inspections app (phase 6).


def metric_statuses(equipment, reading):
    """{metric_key: NORMAL/WARNING/CRITICAL} for one reading."""
    if reading is None:
        return {}
    return {
        metric.key: classify(reading.value_for(metric), *equipment.thresholds(metric))
        for metric in METRICS
    }


def recompute_health(equipment):
    """
    Recalculate and store health from the latest reading and open defects.
    Called after inspections and maintenance; nothing ever forces a machine healthy.
    """
    latest = equipment.readings.order_by("-recorded_at", "-id").first()
    statuses = metric_statuses(equipment, latest).values()
    equipment.health_status = health_from(worst(statuses), has_open_high_defect(equipment))
    equipment.save(update_fields=["health_status", "updated_at"])
    return equipment.health_status


def make_qr_png(text, box_size=10):
    """Return a PNG (bytes) of a QR code for `text`. Generated in memory, never stored."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(image_factory=PilImage, fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def set_sim_mode(equipment, mode):
    """Change how the simulator behaves for this machine (demo control)."""
    equipment.sim_mode = mode
    equipment.full_clean(validate_unique=False, validate_constraints=False)
    equipment.save(update_fields=["sim_mode", "updated_at"])
    return equipment


def set_active(equipment, active):
    """
    Deactivate or reactivate a machine. Nothing is deleted: readings, alerts and
    inspections stay as history. Inactive machines are skipped by the simulator
    and left out of the health counts.
    """
    equipment.is_active = active
    equipment.save(update_fields=["is_active", "updated_at"])
    return equipment
