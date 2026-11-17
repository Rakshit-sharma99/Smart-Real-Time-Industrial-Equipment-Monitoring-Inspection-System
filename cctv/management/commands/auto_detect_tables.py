"""
Auto-detect restaurant tables, boundaries, and seating capacity from AI:

    python manage.py auto_detect_tables [--camera ID] [--source PATH_OR_DEVICE]
"""
import cv2
from django.core.management.base import BaseCommand, CommandError

from cctv.detectors import available_detector
from cctv.models import Camera
from cctv.services import auto_calibrate_zones


class Command(BaseCommand):
    help = "Run AI table segmentation & chair proximity mapping to auto-create table zones and seating capacity."

    def add_arguments(self, parser):
        parser.add_argument("--camera", type=int, help="Camera ID (defaults to the first active camera)")
        parser.add_argument("--source", type=str, help="Optional image or video file path to run calibration on")

    def handle(self, *args, **options):
        cameras = Camera.objects.filter(is_active=True).select_related("restaurant")
        if options["camera"]:
            cameras = cameras.filter(pk=options["camera"])
        camera = cameras.first()
        if not camera:
            raise CommandError("No active camera found.")

        frame = None
        if options["source"]:
            frame = cv2.imread(options["source"])
            if frame is None:
                cap = cv2.VideoCapture(options["source"])
                if cap.isOpened():
                    ok, frame = cap.read()
                    cap.release()
            if frame is None:
                raise CommandError(f"Could not load image or video from source: {options['source']}")

        self.stdout.write(f"Running AI table segmentation on camera '{camera.name}' ({camera.restaurant.name})...")
        detector = available_detector()
        self.stdout.write(f"Using detector: {detector.name}")

        try:
            summary = auto_calibrate_zones(camera, frame=frame, detector=detector)
        except Exception as exc:
            raise CommandError(f"Calibration failed: {exc}")

        self.stdout.write(self.style.SUCCESS(
            f"Successfully auto-detected {summary['tables_detected']} table(s) with total capacity of {summary['total_capacity']} seats!"
        ))
        for d in summary["details"]:
            self.stdout.write(
                f"  - {d['table']}: {d['shape'].upper()} ({d['capacity']} seats, {d['points_count']} polygon points, circularity: {d['circularity']})"
            )
