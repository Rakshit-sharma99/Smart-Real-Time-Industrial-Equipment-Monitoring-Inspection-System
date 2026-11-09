from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from tables.models import Restaurant, RestaurantTable


class Camera(models.Model):
    """
    One video source. The worker (manage.py run_cameras) reads it; Django only
    stores its latest frame and results. The source string may contain RTSP
    credentials: it is never sent to the browser (see masked_source).
    """

    class SourceType(models.TextChoices):
        RTSP = "RTSP", "RTSP / IP camera stream"
        HTTP = "HTTP", "HTTP video or MJPEG URL"
        DEVICE = "DEVICE", "Local camera device (webcam)"
        FILE = "FILE", "Video file (demo)"

    class Status(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Not started"
        ONLINE = "ONLINE", "Live"
        DEMO = "DEMO", "Demo video"
        OFFLINE = "OFFLINE", "Disconnected"

    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name="cameras")
    name = models.CharField(max_length=80)
    source_type = models.CharField(max_length=10, choices=SourceType.choices, default=SourceType.RTSP)
    source = models.CharField(
        "Stream source", max_length=500,
        help_text="rtsp://user:pass@host/stream, an http(s) URL, a device index such as 0, or a video file path.",
    )
    is_active = models.BooleanField("Active", default=True)
    detection_interval_seconds = models.PositiveSmallIntegerField(
        "Detection interval (seconds)", default=2, validators=[MinValueValidator(1), MaxValueValidator(60)]
    )
    min_confidence = models.DecimalField(
        "Minimum detection confidence", max_digits=3, decimal_places=2, default=0.5,
        validators=[MinValueValidator(0.1), MaxValueValidator(1.0)],
    )
    frames_to_confirm = models.PositiveSmallIntegerField(
        "Samples before a change is applied", default=3, validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="A table must look the same in this many consecutive samples before its status changes.",
    )
    # Written by the worker through the API.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN)
    last_frame_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    detector_name = models.CharField(max_length=80, blank=True)
    detects_dishes = models.BooleanField(default=False, help_text="True when the running detector can see dishes.")
    frame_width = models.PositiveSmallIntegerField(default=0)
    frame_height = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["restaurant", "name"], name="unique_camera_name_per_restaurant")]

    def __str__(self):
        return self.name

    def clean(self):
        source = (self.source or "").strip()
        if self.source_type == self.SourceType.DEVICE and not source.isdigit():
            raise ValidationError({"source": "A device source is a number, e.g. 0 for the first webcam."})
        if self.source_type in (self.SourceType.RTSP, self.SourceType.HTTP) and "://" not in source:
            raise ValidationError({"source": "Enter a full URL, e.g. rtsp://user:pass@192.168.1.20:554/stream1."})

    @property
    def is_demo(self):
        return self.source_type == self.SourceType.FILE

    @property
    def stale_after(self):
        return timedelta(seconds=self.detection_interval_seconds * 3 + 10)

    @property
    def is_receiving(self):
        """Frames arrived recently. False means DISCONNECTED regardless of the stored status."""
        return bool(self.last_frame_at and timezone.now() - self.last_frame_at < self.stale_after)

    @property
    def live_status(self):
        """What the console shows: ONLINE / DEMO only while frames keep arriving."""
        if not self.is_receiving:
            return self.Status.OFFLINE if self.last_frame_at or self.last_error else self.Status.UNKNOWN
        return self.Status.DEMO if self.is_demo else self.Status.ONLINE

    @property
    def masked_source(self):
        """The source without credentials, safe to show in the UI."""
        if "://" not in self.source:
            return self.source
        parts = urlsplit(self.source)
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        if parts.username:
            host = "•••@" + host
        return urlunsplit((parts.scheme, host, parts.path, "", ""))


class TableZone(models.Model):
    """Where one table is in one camera's picture: a polygon in normalised (0-1) coordinates."""

    camera = models.ForeignKey(Camera, on_delete=models.CASCADE, related_name="zones")
    table = models.ForeignKey(RestaurantTable, on_delete=models.CASCADE, related_name="zones")
    points = models.JSONField(help_text="[[x, y], ...] with x and y between 0 and 1, at least 3 points.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["table__number"]
        constraints = [models.UniqueConstraint(fields=["camera", "table"], name="one_zone_per_table_per_camera")]

    def __str__(self):
        return f"{self.camera}: {self.table}"

    def clean(self):
        points = self.points
        valid = (
            isinstance(points, list) and len(points) >= 3
            and all(isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in p)
                    for p in points)
        )
        if not valid:
            raise ValidationError({"points": "A zone needs at least 3 points with coordinates between 0 and 1."})
        if self.table_id and self.camera_id and self.table.restaurant_id != self.camera.restaurant_id:
            raise ValidationError({"table": "This table belongs to another restaurant."})
