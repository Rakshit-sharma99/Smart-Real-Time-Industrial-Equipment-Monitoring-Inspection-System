"""Camera results on the Django side: frame cache, status, and applying confirmed zone changes."""
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core import realtime
from inspections import services as inspection_services

from .models import Camera, TableZone

FRAME_TTL = 30  # seconds a frame is kept; after that the console shows "no recent frame"


def frame_key(camera_id):
    return f"camera-frame-{camera_id}"


def results_key(camera_id):
    return f"camera-results-{camera_id}"


def store_frame(camera, jpeg_bytes, width, height):
    cache.set(frame_key(camera.pk), jpeg_bytes, FRAME_TTL)
    now = timezone.now()
    Camera.objects.filter(pk=camera.pk).update(
        status=Camera.Status.DEMO if camera.is_demo else Camera.Status.ONLINE, last_frame_at=now, last_error="",
        frame_width=width, frame_height=height,
    )
    camera.last_frame_at, camera.frame_width, camera.frame_height = now, width, height


def latest_frame(camera):
    return cache.get(frame_key(camera.pk))


def store_results(camera, zone_results):
    """zone_results: {zone_id: {"people", "dishes", "confidence", "occupied", "dishes_present"}}"""
    cache.set(results_key(camera.pk), {"at": timezone.now().isoformat(), "zones": zone_results}, FRAME_TTL)


def latest_results(camera):
    return cache.get(results_key(camera.pk)) or {"at": None, "zones": {}}


def set_detector(camera, name, detects_dishes):
    Camera.objects.filter(pk=camera.pk).update(detector_name=name, detects_dishes=detects_dishes)


def mark_offline(camera, error):
    """The worker lost the stream. Tables are left exactly as they are."""
    Camera.objects.filter(pk=camera.pk).update(status=Camera.Status.OFFLINE, last_error=error[:300])
    realtime.publish("camera.changed", restaurant_id=camera.restaurant_id, topics=["cameras"], everyone=True,
                     data={"camera": camera.pk, "status": Camera.Status.OFFLINE})


def apply_zone_changes(camera, changes):
    """
    Confirmed (temporally smoothed) changes from the worker:
    [{"zone": id, "occupied": bool, "dishes": bool|None, "confidence": float}]
    Each one becomes a camera inspection that updates the table.
    """
    zones = {z.pk: z for z in TableZone.objects.filter(camera=camera).select_related("table__restaurant")}
    applied = []
    for change in changes:
        zone = zones.get(change["zone"])
        if zone is None or not zone.table.is_active:
            continue
        with transaction.atomic():
            inspection = inspection_services.record_detection(
                zone.table, customers_present=change["occupied"], dishes_present=change.get("dishes"),
                confidence=change.get("confidence"), device_id=camera.name,
            )
        applied.append({"table": zone.table.label, "inspection": inspection.reference, "status": inspection.resulting_status})
    realtime.publish("camera.changed", restaurant_id=camera.restaurant_id, topics=["cameras"], everyone=True,
                     data={"camera": camera.pk, "status": camera.live_status})
    return applied


def console_state(camera, tables_by_id):
    """What the Live CCTV page polls once a second: camera status, frame time and per-zone labels."""
    results = latest_results(camera)
    zones = []
    for zone in camera.zones.select_related("table"):
        table = tables_by_id.get(zone.table_id)
        detection = results["zones"].get(str(zone.pk)) or results["zones"].get(zone.pk) or {}
        zones.append({
            "id": zone.pk, "table": zone.table.label, "points": zone.points,
            "status": getattr(table, "status", None), "status_label": getattr(table, "status_label", ""),
            "people": detection.get("people"), "dishes": detection.get("dishes"), "confidence": detection.get("confidence"),
        })
    return {
        "camera": camera.pk, "name": camera.name, "status": camera.live_status,
        "status_label": Camera.Status(camera.live_status).label, "is_demo": camera.is_demo,
        "last_frame_at": camera.last_frame_at.isoformat() if camera.last_frame_at else None,
        "has_frame": latest_frame(camera) is not None, "detector": camera.detector_name,
        "detects_dishes": camera.detects_dishes, "last_error": camera.last_error,
        "results_at": results["at"], "zones": zones,
    }
