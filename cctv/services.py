"""Camera results on the Django side: frame cache, status, and applying confirmed zone changes."""
import cv2
import numpy as np
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core import realtime
from inspections import services as inspection_services
from tables.models import RestaurantTable, TableStatus

from .analysis import analyze_scene_geometry, classify_table_shape
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


def store_results(camera, zone_results, room_capacity=None, detected_tables=None):
    """zone_results: {zone_id: {"people", "dishes", "confidence", "occupied", "dishes_present"}}"""
    payload = {
        "at": timezone.now().isoformat(),
        "zones": zone_results,
        "room_capacity": room_capacity,
        "detected_tables": detected_tables,
    }
    cache.set(results_key(camera.pk), payload, FRAME_TTL)


def latest_results(camera):
    return cache.get(results_key(camera.pk)) or {"at": None, "zones": {}, "room_capacity": None, "detected_tables": None}


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
    [{"zone": id, "occupied": bool, "dishes": bool|None, "confidence": float, "auto_mark_clean": bool}]
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
                auto_mark_clean=change.get("auto_mark_clean", False),
            )
        applied.append({"table": zone.table.label, "inspection": inspection.reference, "status": inspection.resulting_status})
    realtime.publish("camera.changed", restaurant_id=camera.restaurant_id, topics=["cameras"], everyone=True,
                     data={"camera": camera.pk, "status": camera.live_status})
    return applied


def console_state(camera, tables_by_id):
    """What the Live CCTV page polls once a second: camera status, frame time and per-zone labels."""
    results = latest_results(camera)
    zones = []
    for zone in camera.zones.select_related("table__assigned_waiter"):
        table = tables_by_id.get(zone.table_id)
        detection = results["zones"].get(str(zone.pk)) or results["zones"].get(zone.pk) or {}
        waiter = getattr(table, "assigned_waiter", None)
        waiter_name = (waiter.get_full_name() or waiter.username) if waiter else ""
        capacity = getattr(table, "capacity", zone.table.capacity)
        shape, _ = classify_table_shape(zone.points)
        zones.append({
            "id": zone.pk, "table": zone.table.label, "points": zone.points,
            "shape": shape, "capacity": capacity,
            "waiter_name": waiter_name,
            "status": getattr(table, "status", None), "status_label": getattr(table, "status_label", ""),
            "people": detection.get("people"), "dishes": detection.get("dishes"), "confidence": detection.get("confidence"),
        })

    active_tables = [t for t in tables_by_id.values() if getattr(t, "is_active", True)]
    total_capacity = sum(getattr(t, "capacity", 0) for t in active_tables)
    occupied_seats = sum(getattr(t, "capacity", 0) for t in active_tables if getattr(t, "status", None) == TableStatus.OCCUPIED)
    available_seats = sum(getattr(t, "capacity", 0) for t in active_tables if getattr(t, "status", None) == TableStatus.READY)

    return {
        "camera": camera.pk, "name": camera.name, "status": camera.live_status,
        "status_label": Camera.Status(camera.live_status).label, "is_demo": camera.is_demo,
        "last_frame_at": camera.last_frame_at.isoformat() if camera.last_frame_at else None,
        "has_frame": latest_frame(camera) is not None, "detector": camera.detector_name,
        "detects_dishes": camera.detects_dishes, "last_error": camera.last_error,
        "results_at": results["at"], "zones": zones,
        "total_restaurant_capacity": total_capacity,
        "occupied_seats": occupied_seats,
        "available_seats": available_seats,
        "total_tables_count": len(active_tables),
        "detected_room_capacity": results.get("room_capacity"),
        "detected_tables_count": results.get("detected_tables"),
    }


def auto_calibrate_zones(camera, frame=None, detector=None):
    """
    Auto-detect tables and nearby chairs using AI segmentation.
    Generates polygon zones and computes seating capacity per table.
    """
    if detector is None:
        from .detectors import available_detector
        detector = available_detector()

    if frame is None:
        frame_bytes = latest_frame(camera)
        if frame_bytes:
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        else:
            src = camera.source.strip()
            src = int(src) if camera.source_type == Camera.SourceType.DEVICE else src
            cap = cv2.VideoCapture(src)
            if cap.isOpened():
                ok, frame = cap.read()
                cap.release()
                if not ok:
                    frame = None

    if frame is None:
        raise ValueError("No video frame available to calibrate from.")

    height, width = frame.shape[:2]
    scene = detector.detect_scene(frame, min_confidence=float(camera.min_confidence))
    geom = analyze_scene_geometry(scene.tables, scene.chairs)

    if not geom.tables:
        return {
            "status": "warning",
            "message": "No tables detected in frame.",
            "tables_detected": 0,
            "zones_created": 0,
            "total_capacity": 0,
            "details": [],
        }

    # Sort detected tables top-to-bottom, left-to-right
    sorted_tables = sorted(geom.tables, key=lambda t: (
        sum(p[1] for p in t.polygon) / len(t.polygon),
        sum(p[0] for p in t.polygon) / len(t.polygon),
    ))

    restaurant_tables = list(
        RestaurantTable.objects.filter(restaurant=camera.restaurant, is_active=True).order_by("number")
    )
    if not restaurant_tables:
        raise ValueError("No active restaurant tables exist to map to.")

    updated_zones = []
    zone_details = []

    with transaction.atomic():
        camera.zones.all().delete()
        for i, t_geom in enumerate(sorted_tables):
            if i >= len(restaurant_tables):
                break
            r_table = restaurant_tables[i]
            norm_points = [
                [round(max(0.0, min(1.0, float(x) / width)), 4),
                 round(max(0.0, min(1.0, float(y) / height)), 4)]
                for x, y in t_geom.polygon
            ]
            if len(norm_points) < 3:
                continue

            zone = TableZone.objects.create(camera=camera, table=r_table, points=norm_points)
            if t_geom.capacity > 0:
                r_table.capacity = t_geom.capacity
                r_table.save(update_fields=["capacity"])

            updated_zones.append(zone)
            zone_details.append({
                "table": r_table.label,
                "table_number": r_table.number,
                "shape": t_geom.shape,
                "capacity": t_geom.capacity,
                "circularity": t_geom.circularity,
                "points_count": len(norm_points),
            })

    return {
        "status": "ok",
        "tables_detected": len(sorted_tables),
        "zones_created": len(updated_zones),
        "total_capacity": sum(z["capacity"] for z in zone_details),
        "details": zone_details,
    }
