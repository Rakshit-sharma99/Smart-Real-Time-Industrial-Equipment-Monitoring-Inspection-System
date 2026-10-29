"""Equipment business logic: QR images and activation."""
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def make_qr_png(text, box_size=10):
    """Return a PNG (bytes) of a QR code for `text`. Generated in memory, never stored."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def set_active(equipment, active):
    """
    Deactivate or reactivate a machine. Nothing is deleted: readings, alerts and
    inspections stay as history. Inactive machines are skipped by the simulator
    and left out of the health counts.
    """
    equipment.is_active = active
    equipment.save(update_fields=["is_active", "updated_at"])
    return equipment
