"""
Push updates to every open dashboard over WebSockets (Django Channels).

Called from transaction.on_commit, so browsers only hear about data that was
really saved. Payloads are deliberately small and contain no user data:
equipment code/name/status, metric values, time, source, alert id/severity/metric
and summary counts.
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from equipment.models import Equipment

from .models import Alert

logger = logging.getLogger(__name__)

DASHBOARD_GROUP = "dashboard"


def summary_counts():
    counts = Equipment.objects.status_counts()
    counts["active_alerts"] = Alert.objects.open().count()
    return counts


def send_to_dashboard(payload):
    """Send one message to the 'dashboard' group. Never raises: a failed broadcast must not break ingest."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            DASHBOARD_GROUP, {"type": "dashboard.message", "payload": payload}
        )
    except Exception:  # noqa: BLE001 - log and carry on
        logger.exception("Could not broadcast to the dashboard group")


def reading_message(reading, equipment, summary):
    return {
        "type": "reading",
        "equipment": {
            "code": equipment.equipment_code,
            "name": equipment.name,
            "status": equipment.display_status,
        },
        "values": {
            "temperature": str(reading.temperature_c),
            "vibration": str(reading.vibration_mm_s),
            "pressure": str(reading.pressure_bar),
        },
        "recorded_at": reading.recorded_at.isoformat(),
        "source": reading.source,
        "summary": summary,
    }


def alert_message(event, alert, summary):
    return {
        "type": "alert",
        "event": event,  # created | escalated | acknowledged | resolved
        "alert": {
            "id": alert.pk,
            "severity": alert.severity,
            "metric": alert.metric,
            "status": alert.status,
        },
        "equipment": {"code": alert.equipment.equipment_code, "name": alert.equipment.name},
        "summary": summary,
    }


def broadcast_reading(reading, equipment, alert_events):
    summary = summary_counts()
    send_to_dashboard(reading_message(reading, equipment, summary))
    for event, alert in alert_events:
        send_to_dashboard(alert_message(event, alert, summary))


def broadcast_alert(event, alert):
    send_to_dashboard(alert_message(event, alert, summary_counts()))
