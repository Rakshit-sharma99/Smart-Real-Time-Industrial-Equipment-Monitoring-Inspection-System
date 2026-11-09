"""
Pure logic shared by the worker and the tests: zone matching and temporal smoothing.
No Django, no OpenCV here, so it is easy to test.
"""
from dataclasses import dataclass, field


def point_in_polygon(point, polygon):
    """Ray casting. polygon = [(x, y), ...] in the same units as point."""
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def scale_polygon(points, width, height):
    return [(x * width, y * height) for x, y in points]


def assign_to_zones(detections, zones, width, height):
    """
    {zone_id: {"people": n, "dishes": n, "confidence": max}} for the detections whose
    anchor point falls inside a zone. zones = [(zone_id, normalised points), ...].
    """
    pixel_zones = [(zone_id, scale_polygon(points, width, height)) for zone_id, points in zones]
    results = {zone_id: {"people": 0, "dishes": 0, "confidence": 0.0} for zone_id, _ in zones}
    for det in detections:
        for zone_id, polygon in pixel_zones:
            if point_in_polygon(det.anchor, polygon):
                bucket = results[zone_id]
                bucket["people" if det.label == "person" else "dishes"] += 1
                bucket["confidence"] = max(bucket["confidence"], det.confidence)
                break
    return results


@dataclass
class ZoneTracker:
    """
    Temporal smoothing for one table zone: a new observation must repeat
    `required` times in a row before it becomes the published state. This
    filters out people walking past, brief occlusions and single bad frames.
    """

    required: int = 3
    published: dict = field(default_factory=dict)  # {"occupied": bool, "dishes": bool|None}
    candidate: dict = field(default_factory=dict)
    streak: int = 0

    def observe(self, occupied, dishes):
        """Returns the new state to publish, or None when nothing (stable) changed."""
        observation = {"occupied": bool(occupied), "dishes": dishes}
        if observation == self.published:
            self.candidate, self.streak = {}, 0
            return None
        if observation == self.candidate:
            self.streak += 1
        else:
            self.candidate, self.streak = observation, 1
        if self.streak >= self.required:
            self.published, self.candidate, self.streak = observation, {}, 0
            return observation
        return None

    def reset(self):
        """Camera lost: forget the pending candidate but keep what was published (nothing is assumed)."""
        self.candidate, self.streak = {}, 0
