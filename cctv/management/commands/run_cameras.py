"""
Background video worker. Runs in its own process, next to the web server:

    python manage.py run_cameras [--camera ID] [--url http://127.0.0.1:8000] [--once]

For every active camera of every restaurant it:
  1. opens the source with OpenCV (RTSP/HTTP URL, webcam index, or a video file in a loop)
  2. every `detection_interval_seconds` grabs a frame and runs the detector
  3. maps detections to the camera's table zones
  4. smooths each zone over time (a change must repeat `frames_to_confirm` times)
  5. POSTs the frame + zone results (+ confirmed changes) to /api/v1/cameras/<id>/frame/

Django never processes video inside a request: it only stores the latest frame and applies
the confirmed changes. When a stream drops, the worker reports OFFLINE and nothing about
the tables changes. Ctrl+C stops the worker.
"""
import base64
import json
import time
import urllib.error
import urllib.request

import cv2
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cctv.analysis import ZoneTracker, assign_to_zones
from cctv.detectors import available_detector
from cctv.models import Camera

JPEG_QUALITY = 70
MAX_FRAME_WIDTH = 960


class ApiClient:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path, payload):
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Token {self.token}"},
        )
        with self.opener.open(request, timeout=15) as response:
            return json.loads(response.read().decode())


class CameraRunner:
    """Everything about one camera: capture, detect, smooth, report."""

    def __init__(self, camera, detector, client, log):
        self.camera, self.detector, self.client, self.log = camera, detector, client, log
        self.capture = None
        self.trackers = {}
        self.next_sample_at = 0.0
        self.failures = 0
        self.disabled = False

    def source(self):
        src = self.camera.source.strip()
        return int(src) if self.camera.source_type == Camera.SourceType.DEVICE else src

    def open(self):
        self.capture = cv2.VideoCapture(self.source())
        if not self.capture.isOpened():
            self.capture = None
            return False
        return True

    def read_frame(self):
        ok, frame = self.capture.read()
        if not ok and self.camera.source_type == Camera.SourceType.FILE:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop the demo video
            ok, frame = self.capture.read()
        return frame if ok else None

    def tick(self, now):
        if self.disabled or now < self.next_sample_at:
            return
        self.next_sample_at = now + self.camera.detection_interval_seconds
        self.camera.refresh_from_db()  # zones and settings may have changed in the UI
        if self.capture is None and not self.open():
            self.report_offline("Cannot open the video source")
            return
        frame = self.read_frame()
        if frame is None:
            self.capture.release()
            self.capture = None
            self.report_offline("No frame received from the source")
            return
        self.failures = 0
        height, width = frame.shape[:2]
        if width > MAX_FRAME_WIDTH:
            scale = MAX_FRAME_WIDTH / width
            frame = cv2.resize(frame, (MAX_FRAME_WIDTH, int(height * scale)))
            height, width = frame.shape[:2]

        detections = self.detector.detect(frame, float(self.camera.min_confidence))
        zones = [(zone.pk, zone.points) for zone in self.camera.zones.all()]
        per_zone = assign_to_zones(detections, zones, width, height)
        results, changes = {}, []
        for zone_id, counts in per_zone.items():
            occupied = counts["people"] > 0
            dishes = (counts["dishes"] > 0) if self.detector.dishes_supported else None
            results[zone_id] = {**counts, "occupied": occupied, "dishes_present": dishes}
            tracker = self.trackers.setdefault(zone_id, ZoneTracker(required=self.camera.frames_to_confirm))
            confirmed = tracker.observe(occupied, dishes)
            if confirmed is not None:
                changes.append({"zone": zone_id, **confirmed, "confidence": counts["confidence"] or None})

        ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        payload = {
            "frame_jpeg": base64.b64encode(jpeg.tobytes()).decode() if ok else "", "width": width, "height": height,
            "detector": self.detector.name, "detects_dishes": self.detector.dishes_supported,
            "zones": results, "changes": changes,
            "detections": [{"label": d.label, "box": [round(v) for v in d.box], "confidence": round(d.confidence, 2)} for d in detections],
        }
        try:
            reply = self.client.post(f"/api/v1/cameras/{self.camera.pk}/frame/", payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # camera deleted or deactivated in the UI: stop reading it, keep the others running
                self.log(f"[{self.camera.name}] no longer exists or is inactive on the server; stopping this camera.")
                self.close()
                self.disabled = True
                return
            raise CommandError(f"Server rejected the frame (HTTP {exc.code}): {exc.read().decode(errors='replace')[:200]}")
        applied = reply.get("applied", [])
        summary = f"{len(detections)} detections, {sum(1 for r in results.values() if r['occupied'])}/{len(results)} zones occupied"
        if applied:
            summary += " | updated: " + ", ".join(f"{a['table']} -> {a['status']}" for a in applied)
        self.log(f"[{self.camera.name}] {summary}")

    def report_offline(self, error):
        self.failures += 1
        for tracker in self.trackers.values():
            tracker.reset()
        if self.failures in (1, 10) or self.failures % 60 == 0:
            self.log(f"[{self.camera.name}] OFFLINE: {error} (tables unchanged)")
        try:
            self.client.post(f"/api/v1/cameras/{self.camera.pk}/status/", {"status": "OFFLINE", "error": error})
        except (urllib.error.URLError, OSError):
            pass
        self.next_sample_at = time.monotonic() + min(30, 2 * self.failures)

    def close(self):
        if self.capture is not None:
            self.capture.release()


class Command(BaseCommand):
    help = "Read the cameras, detect people/dishes per table zone and send results to the server. Ctrl+C stops."

    def add_arguments(self, parser):
        parser.add_argument("--camera", type=int, help="Only this camera id.")
        parser.add_argument("--url", default="http://127.0.0.1:8000", help="Web server base URL.")
        parser.add_argument("--once", action="store_true", help="Process one sample per camera and exit.")

    def handle(self, *args, **options):
        if not settings.DETECTION_API_TOKEN:
            raise CommandError("DETECTION_API_TOKEN is empty in .env.")
        cameras = Camera.objects.filter(is_active=True).select_related("restaurant")
        if options["camera"]:
            cameras = cameras.filter(pk=options["camera"])
        if not cameras.exists():
            raise CommandError("No active cameras. Add one under Settings > Cameras.")
        detector = available_detector()
        client = ApiClient(options["url"], settings.DETECTION_API_TOKEN)
        log = lambda text: (self.stdout.write(f"{time.strftime('%H:%M:%S')} {text}"), self.stdout.flush())  # noqa: E731
        log(f"Detector: {detector.name} (dishes {'supported' if detector.dishes_supported else 'NOT detectable'})")
        runners = [CameraRunner(camera, detector, client, log) for camera in cameras]
        try:
            while True:
                now = time.monotonic()
                for runner in runners:
                    try:
                        runner.tick(now)
                    except urllib.error.URLError as exc:
                        log(f"Server not reachable at {options['url']} ({exc.reason}). Retrying...")
                        time.sleep(5)
                if options["once"]:
                    break
                time.sleep(0.2)
        except KeyboardInterrupt:
            log("Stopping (Ctrl+C).")
        finally:
            for runner in runners:
                runner.close()
