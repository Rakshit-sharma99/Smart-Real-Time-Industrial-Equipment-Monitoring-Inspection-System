"""
Pure logic shared by the worker, geometry engine and tests:
  - Table shape classification (circular vs rectangular)
  - Chair proximity mapping & per-table seating capacity
  - Room/restaurant total capacity aggregation
  - Zone matching and temporal smoothing
No Django, no OpenCV here, so it is easy to test deterministically.
"""
from dataclasses import dataclass, field
import math

CIRCULAR = "circular"
RECTANGULAR = "rectangular"


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


def polygon_area(polygon):
    """Calculate absolute area of a polygon using Shoelace formula."""
    if len(polygon) < 3:
        return 0.0
    n = len(polygon)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]
    return abs(area) / 2.0


def polygon_perimeter(polygon):
    """Calculate perimeter of a polygon."""
    if len(polygon) < 2:
        return 0.0
    n = len(polygon)
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = polygon[j][0] - polygon[i][0]
        dy = polygon[j][1] - polygon[i][1]
        perimeter += math.hypot(dx, dy)
    return perimeter


def polygon_centroid(polygon):
    """Calculate (cx, cy) centroid of a polygon."""
    if not polygon:
        return (0.0, 0.0)
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def expand_polygon(polygon, scale=1.35):
    """Expand polygon outwards from its centroid by a scale factor (to capture chairs)."""
    if not polygon:
        return []
    cx, cy = polygon_centroid(polygon)
    return [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in polygon]


def classify_table_shape(polygon):
    """
    Classify polygon as 'circular' or 'rectangular' based on circularity:
      Circularity = (4 * pi * Area) / (Perimeter^2)
    A circle is ~1.0; a square is ~pi/4 = ~0.785; elongated rectangles < 0.75.
    Threshold: >= 0.82 is considered circular.
    """
    if len(polygon) < 3:
        return RECTANGULAR, 0.0
    area = polygon_area(polygon)
    perimeter = polygon_perimeter(polygon)
    if perimeter <= 1e-9:
        return RECTANGULAR, 0.0
    circularity = (4.0 * math.pi * area) / (perimeter * perimeter)
    shape = CIRCULAR if circularity >= 0.82 else RECTANGULAR
    return shape, round(circularity, 3)


@dataclass
class TableGeometry:
    index: int
    polygon: list
    shape: str  # "circular" or "rectangular"
    circularity: float
    capacity: int  # count of mapped chairs
    confidence: float = 1.0


@dataclass
class SceneGeometry:
    tables: list = field(default_factory=list)  # list of TableGeometry
    total_tables: int = 0
    total_capacity: int = 0


def _get_polygon(table):
    if hasattr(table, "polygon") and table.polygon:
        return [tuple(p) for p in table.polygon]
    if hasattr(table, "box") and table.box:
        x1, y1, x2, y2 = table.box
        return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    if isinstance(table, (list, tuple)):
        return [tuple(p) for p in table]
    return []


def _get_point(obj):
    if hasattr(obj, "anchor"):
        return obj.anchor
    if hasattr(obj, "box") and obj.box:
        x1, y1, x2, y2 = obj.box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    if isinstance(obj, (list, tuple)) and len(obj) == 2:
        return (float(obj[0]), float(obj[1]))
    return (0.0, 0.0)


def map_chairs_to_tables(tables, chairs, chair_buffer_scale=1.35):
    """
    Assign each detected chair to the most appropriate table based on:
    1. Being inside the expanded table buffer zone.
    2. Centroid distance to the table.
    Returns: {table_index: chair_count}
    """
    if not tables:
        return {}

    table_data = []
    for idx, table in enumerate(tables):
        poly = _get_polygon(table)
        centroid = polygon_centroid(poly)
        expanded = expand_polygon(poly, scale=chair_buffer_scale)
        table_data.append({
            "idx": idx,
            "poly": poly,
            "centroid": centroid,
            "expanded": expanded,
            "chairs": 0,
        })

    for chair in chairs:
        chair_pt = _get_point(chair)
        best_table = None
        best_dist = float("inf")

        # First check if chair is inside expanded polygon
        candidates = []
        for td in table_data:
            if point_in_polygon(chair_pt, td["expanded"]):
                dist = math.hypot(chair_pt[0] - td["centroid"][0], chair_pt[1] - td["centroid"][1])
                candidates.append((dist, td))

        if candidates:
            candidates.sort(key=lambda c: c[0])
            best_table = candidates[0][1]
        else:
            # Fallback: nearest table centroid
            for td in table_data:
                dist = math.hypot(chair_pt[0] - td["centroid"][0], chair_pt[1] - td["centroid"][1])
                if dist < best_dist:
                    best_dist = dist
                    best_table = td

        if best_table is not None:
            best_table["chairs"] += 1

    return {td["idx"]: td["chairs"] for td in table_data}


def analyze_scene_geometry(tables, chairs, default_min_capacity=2):
    """
    Analyze all detected tables and chairs:
    - Classifies shape (circular vs rectangular)
    - Maps chairs to compute table seating capacity
    - Aggregates total room seating capacity
    """
    chair_counts = map_chairs_to_tables(tables, chairs)
    geometries = []
    total_capacity = 0

    for idx, table in enumerate(tables):
        poly = _get_polygon(table)
        shape, circularity = classify_table_shape(poly)
        # Capacity is the detected chairs, or default_min_capacity if no chairs visible
        detected_chairs = chair_counts.get(idx, 0)
        capacity = max(detected_chairs, default_min_capacity) if detected_chairs == 0 else detected_chairs
        conf = getattr(table, "confidence", 1.0)

        geom = TableGeometry(
            index=idx + 1,
            polygon=poly,
            shape=shape,
            circularity=circularity,
            capacity=capacity,
            confidence=conf,
        )
        geometries.append(geom)
        total_capacity += capacity

    return SceneGeometry(
        tables=geometries,
        total_tables=len(geometries),
        total_capacity=total_capacity,
    )


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
