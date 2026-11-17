"""CCTV: zone matching, temporal smoothing, worker API, console pages and permissions."""
import base64
import io
import json

import numpy
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.testutils import make_manager, make_receptionist, make_restaurant, make_table, make_waiter
from inspections.models import TableInspection
from tables import services as table_services
from tables.models import RestaurantTable, TableStatus

from . import services
from .analysis import (
    CIRCULAR,
    RECTANGULAR,
    ZoneTracker,
    analyze_scene_geometry,
    assign_to_zones,
    classify_table_shape,
    expand_polygon,
    map_chairs_to_tables,
    point_in_polygon,
    polygon_area,
    polygon_perimeter,
)
from .detectors import (
    CHAIR,
    DISHES,
    PERSON,
    TABLE,
    Detection,
    HogPersonDetector,
    SceneDetections,
    available_detector,
    box_to_polygon,
    simplify_polygon,
)
from .models import Camera, TableZone

SQUARE = [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]  # normalised zone covering the top-left quarter


def jpeg_base64(width=64, height=48):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (40, 40, 40)).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode()


class AnalysisTests(TestCase):
    def test_point_in_polygon(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(point_in_polygon((5, 5), polygon))
        self.assertFalse(point_in_polygon((15, 5), polygon))

    def test_detections_are_assigned_to_the_zone_they_fall_in(self):
        zones = [(1, SQUARE), (2, [[0.6, 0.6], [0.9, 0.6], [0.9, 0.9], [0.6, 0.9]])]
        detections = [
            Detection("person", (20, 10, 60, 90), 0.9),      # anchor (40, 70) in a 200x200 frame -> zone 1
            Detection("dishes", (140, 140, 160, 160), 0.8),  # centre (150, 150) -> zone 2
            Detection("person", (100, 180, 120, 199), 0.7),  # outside every zone
        ]
        result = assign_to_zones(detections, zones, 200, 200)
        self.assertEqual(result[1], {"people": 1, "dishes": 0, "confidence": 0.9})
        self.assertEqual(result[2], {"people": 0, "dishes": 1, "confidence": 0.8})

    def test_tracker_needs_consecutive_agreement(self):
        tracker = ZoneTracker(required=3)
        self.assertIsNone(tracker.observe(True, None))
        self.assertIsNone(tracker.observe(True, None))
        self.assertEqual(tracker.observe(True, None), {"occupied": True, "dishes": None})
        # One frame without the person (someone walked past the camera) changes nothing.
        self.assertIsNone(tracker.observe(False, None))
        self.assertIsNone(tracker.observe(True, None))
        # The streak restarted, so another two empty frames are not enough either.
        self.assertIsNone(tracker.observe(False, None))
        self.assertIsNone(tracker.observe(False, None))
        self.assertEqual(tracker.observe(False, None), {"occupied": False, "dishes": None})

    def test_reset_keeps_published_state(self):
        tracker = ZoneTracker(required=2)
        tracker.observe(True, None)
        tracker.observe(True, None)
        tracker.observe(False, None)
        tracker.reset()
        self.assertEqual(tracker.published, {"occupied": True, "dishes": None})
        self.assertIsNone(tracker.observe(False, None))  # needs the full streak again

    def test_hog_detector_runs_on_a_real_frame_and_cannot_see_dishes(self):
        detector = HogPersonDetector()
        frame = numpy.zeros((240, 320, 3), dtype=numpy.uint8)
        self.assertEqual(detector.detect(frame), [])  # an empty grey frame has no people
        self.assertFalse(detector.dishes_supported)
        self.assertFalse(available_detector(prefer_yolo=False).dishes_supported)

    def test_detection_polygon_and_scene_structure(self):
        poly = box_to_polygon((10, 20, 100, 80))
        self.assertEqual(len(poly), 4)
        self.assertEqual(poly[0], (10.0, 20.0))

        table_det = Detection(TABLE, (10, 20, 100, 80), 0.95, polygon=poly)
        chair_det = Detection(CHAIR, (5, 10, 25, 40), 0.85)
        person_det = Detection(PERSON, (30, 40, 60, 90), 0.9)
        dishes_det = Detection(DISHES, (40, 45, 55, 60), 0.88)

        scene = SceneDetections(
            tables=[table_det],
            chairs=[chair_det],
            people=[person_det],
            dishes=[dishes_det],
        )
        self.assertEqual(len(scene.tables), 1)
        self.assertEqual(len(scene.chairs), 1)
        self.assertEqual(len(scene.all_occupancy_detections), 2)
        self.assertEqual(table_det.polygon, poly)

        # Simplify polygon
        noisy_pts = [(0, 0), (5, 0.1), (10, 0), (10, 10), (0, 10)]
        simplified = simplify_polygon(noisy_pts)
        self.assertIsNotNone(simplified)
        self.assertLessEqual(len(simplified), len(noisy_pts))

    def test_hog_detector_detect_scene(self):
        detector = HogPersonDetector()
        frame = numpy.zeros((240, 320, 3), dtype=numpy.uint8)
        scene = detector.detect_scene(frame)
        self.assertIsInstance(scene, SceneDetections)
        self.assertEqual(len(scene.people), 0)
        self.assertEqual(len(scene.tables), 0)

    def test_classify_table_shape(self):
        import math
        # 16-point circle around (50, 50)
        circle_poly = [
            (50 + 20 * math.cos(2 * math.pi * i / 16), 50 + 20 * math.sin(2 * math.pi * i / 16))
            for i in range(16)
        ]
        shape_circ, circ_val = classify_table_shape(circle_poly)
        self.assertEqual(shape_circ, CIRCULAR)
        self.assertGreater(circ_val, 0.85)

        # Rectangular table (100 x 40)
        rect_poly = [(0, 0), (100, 0), (100, 40), (0, 40)]
        shape_rect, rect_val = classify_table_shape(rect_poly)
        self.assertEqual(shape_rect, RECTANGULAR)
        self.assertLess(rect_val, 0.82)

    def test_chair_mapping_and_scene_geometry(self):
        # Table 1: at (50, 50) box [20, 20, 80, 80]
        t1 = Detection(TABLE, (20, 20, 80, 80), 0.9, polygon=[(20, 20), (80, 20), (80, 80), (20, 80)])
        # Table 2: at (250, 50) box [220, 20, 280, 80]
        t2 = Detection(TABLE, (220, 20, 280, 80), 0.9, polygon=[(220, 20), (280, 20), (280, 80), (220, 80)])

        # 4 chairs around Table 1
        chairs_t1 = [
            Detection(CHAIR, (50, 10, 60, 20), 0.8),  # top
            Detection(CHAIR, (50, 80, 60, 90), 0.8),  # bottom
            Detection(CHAIR, (10, 50, 20, 60), 0.8),  # left
            Detection(CHAIR, (80, 50, 90, 60), 0.8),  # right
        ]
        # 2 chairs around Table 2
        chairs_t2 = [
            Detection(CHAIR, (250, 10, 260, 20), 0.8),
            Detection(CHAIR, (250, 80, 260, 90), 0.8),
        ]
        all_chairs = chairs_t1 + chairs_t2

        mapping = map_chairs_to_tables([t1, t2], all_chairs)
        self.assertEqual(mapping[0], 4)
        self.assertEqual(mapping[1], 2)

        geom = analyze_scene_geometry([t1, t2], all_chairs)
        self.assertEqual(geom.total_tables, 2)
        self.assertEqual(geom.total_capacity, 6)
        self.assertEqual(geom.tables[0].capacity, 4)
        self.assertEqual(geom.tables[1].capacity, 2)
        self.assertEqual(geom.tables[0].shape, RECTANGULAR)


class CameraCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.restaurant = make_restaurant()
        cls.other_restaurant = make_restaurant("Other", "other")
        cls.manager = make_manager(cls.restaurant)
        cls.waiter = make_waiter(cls.restaurant)
        cls.receptionist = make_receptionist(cls.restaurant)
        cls.outsider = make_manager(cls.other_restaurant, "other_manager")
        cls.table = make_table(cls.restaurant, 12, capacity=4)
        cls.camera = Camera.objects.create(restaurant=cls.restaurant, name="Hall cam", source_type="RTSP",
                                           source="rtsp://admin:secret@10.0.0.5:554/stream1")
        cls.zone = TableZone.objects.create(camera=cls.camera, table=cls.table, points=SQUARE)

    def setUp(self):
        cache.clear()

    def status(self):
        return table_services.status_of(RestaurantTable.objects.get(pk=self.table.pk))


class CameraModelTests(CameraCase):
    def test_credentials_are_masked(self):
        self.assertEqual(self.camera.masked_source, "rtsp://•••@10.0.0.5:554/stream1")
        self.assertNotIn("secret", self.camera.masked_source)

    def test_status_depends_on_recent_frames(self):
        self.assertEqual(self.camera.live_status, Camera.Status.UNKNOWN)
        Camera.objects.filter(pk=self.camera.pk).update(last_error="timeout")
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.live_status, Camera.Status.OFFLINE)

    def test_zone_validation(self):
        bad = TableZone(camera=self.camera, table=self.table, points=[[0, 0], [2, 0], [1, 1]])
        with self.assertRaises(Exception):
            bad.clean()


@override_settings(DETECTION_API_TOKEN="cam-token")
class WorkerApiTests(CameraCase):
    def frame_payload(self, changes=(), zones=None):
        return {
            "frame_jpeg": jpeg_base64(), "width": 64, "height": 48, "detector": "OpenCV HOG people detector",
            "detects_dishes": False, "changes": list(changes),
            "zones": zones or {str(self.zone.pk): {"people": 1, "dishes": 0, "confidence": 0.8, "occupied": True, "dishes_present": None}},
        }

    def post(self, path, payload, token="cam-token"):
        headers = {"HTTP_AUTHORIZATION": f"Token {token}"} if token else {}
        return self.client.post(path, json.dumps(payload), content_type="application/json", **headers)

    def test_frame_requires_token(self):
        url = f"/api/v1/cameras/{self.camera.pk}/frame/"
        self.assertEqual(self.post(url, self.frame_payload(), token=None).status_code, 401)
        self.assertEqual(self.post(url, self.frame_payload(), token="wrong").status_code, 401)

    def test_frame_is_stored_and_served_to_managers_only(self):
        response = self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload())
        self.assertEqual(response.status_code, 201)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.live_status, Camera.Status.ONLINE)
        self.assertEqual(self.camera.detector_name, "OpenCV HOG people detector")
        frame_url = reverse("cctv:frame", args=[self.camera.pk])
        self.client.force_login(self.manager)
        frame = self.client.get(frame_url)
        self.assertEqual(frame.status_code, 200)
        self.assertEqual(frame["Content-Type"], "image/jpeg")
        for user in (self.waiter, self.receptionist):
            self.client.force_login(user)
            self.assertEqual(self.client.get(frame_url).status_code, 403)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(frame_url).status_code, 404)

    def test_non_jpeg_frame_rejected(self):
        payload = self.frame_payload()
        payload["frame_jpeg"] = base64.b64encode(b"not an image").decode()
        self.assertEqual(self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", payload).status_code, 400)

    def test_confirmed_changes_update_the_table_through_a_camera_check(self):
        changes = [{"zone": self.zone.pk, "occupied": True, "dishes": None, "confidence": 0.8}]
        response = self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload(changes))
        self.assertEqual(response.json()["applied"][0]["status"], TableStatus.OCCUPIED)
        self.assertEqual(self.status(), TableStatus.OCCUPIED)
        inspection = TableInspection.objects.get()
        self.assertEqual((inspection.source, inspection.device_id, inspection.dishes_checked), ("CAMERA", "Hall cam", False))
        # People gone, dishes unknown: vacant, but cleanliness is left alone (no detector for dishes).
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": False, "dishes": None}]))
        self.assertEqual(self.status(), TableStatus.READY)
        # With a dish-capable detector: dishes seen -> needs clearing; dishes gone -> cleaning required, never Ready.
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": False, "dishes": True}]))
        self.assertEqual(self.status(), TableStatus.VACANT_NEEDS_CLEARING)
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": False, "dishes": False}]))
        self.assertEqual(self.status(), TableStatus.CLEANING_REQUIRED)

    def test_auto_mark_clean_changes_table_to_ready(self):
        # When auto_mark_clean is True in the confirmed change, blank table becomes READY
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": False, "dishes": True}]))
        self.assertEqual(self.status(), TableStatus.VACANT_NEEDS_CLEARING)
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": False, "dishes": False, "auto_mark_clean": True}]))
        self.assertEqual(self.status(), TableStatus.READY)

    def test_console_state_includes_waiter_and_capacity(self):
        self.table.assigned_waiter = self.waiter
        self.table.save()
        state = services.console_state(self.camera, {self.table.pk: self.table})
        self.assertEqual(len(state["zones"]), 1)
        z = state["zones"][0]
        self.assertEqual(z["waiter_name"], self.waiter.username)
        self.assertEqual(z["capacity"], self.table.capacity)

    def test_camera_offline_never_changes_tables(self):
        self.post(f"/api/v1/cameras/{self.camera.pk}/frame/", self.frame_payload([{"zone": self.zone.pk, "occupied": True, "dishes": None}]))
        response = self.post(f"/api/v1/cameras/{self.camera.pk}/status/", {"status": "OFFLINE", "error": "connection refused"})
        self.assertEqual(response.status_code, 200)
        self.camera.refresh_from_db()
        self.assertEqual(self.camera.status, Camera.Status.OFFLINE)
        self.assertEqual(self.status(), TableStatus.OCCUPIED)  # still occupied: no guessing while blind
        self.assertEqual(TableInspection.objects.count(), 1)

    def test_other_restaurants_zone_is_ignored(self):
        other_table = make_table(self.other_restaurant, 1)
        other_zone = TableZone.objects.create(
            camera=Camera.objects.create(restaurant=self.other_restaurant, name="X", source_type="DEVICE", source="0"),
            table=other_table, points=SQUARE,
        )
        response = self.post(f"/api/v1/cameras/{self.camera.pk}/frame/",
                             self.frame_payload([{"zone": other_zone.pk, "occupied": True, "dishes": None}]))
        self.assertEqual(response.json()["applied"], [])


class ConsoleAndZoneTests(CameraCase):
    def test_console_for_manager_only(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("cctv:console"))
        self.assertContains(page, "Hall cam")
        self.assertContains(page, "•••@10.0.0.5")
        self.assertNotContains(page, "secret")
        state = self.client.get(reverse("cctv:state", args=[self.camera.pk])).json()
        self.assertEqual(state["zones"][0]["table"], "T12")
        self.assertNotIn("source", state)
        for user in (self.waiter, self.receptionist):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("cctv:console")).status_code, 403)

    def test_zone_editor_saves_and_validates(self):
        self.client.force_login(self.manager)
        url = reverse("cctv:save_zones", args=[self.camera.pk])
        response = self.client.post(url, json.dumps([{"table": self.table.pk, "points": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.7]]}]),
                                    content_type="application/json")
        self.assertEqual(response.json()["saved"], 1)
        self.assertEqual(TableZone.objects.get(camera=self.camera).points[0], [0.2, 0.2])
        response = self.client.post(url, json.dumps([{"table": self.table.pk, "points": [[0.2, 0.2]]}]), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        other_table = make_table(self.other_restaurant, 1)
        response = self.client.post(url, json.dumps([{"table": other_table.pk, "points": SQUARE}]), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertContains(self.client.get(reverse("cctv:zones", args=[self.camera.pk])), "Finish zone")

    def test_camera_settings_crud(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("cctv:camera_create"), {
            "name": "Terrace", "source_type": "DEVICE", "source": "0", "is_active": "on",
            "detection_interval_seconds": 2, "min_confidence": "0.5", "frames_to_confirm": 3,
        })
        self.assertRedirects(response, reverse("cctv:camera_list"))
        camera = Camera.objects.get(name="Terrace")
        response = self.client.post(reverse("cctv:camera_create"), {
            "name": "Bad", "source_type": "RTSP", "source": "no-scheme", "detection_interval_seconds": 2,
            "min_confidence": "0.5", "frames_to_confirm": 3,
        })
        self.assertContains(response, "Enter a full URL")
        self.client.post(reverse("cctv:camera_delete", args=[camera.pk]))
        self.assertFalse(Camera.objects.filter(pk=camera.pk).exists())
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("cctv:camera_list")).status_code, 403)
