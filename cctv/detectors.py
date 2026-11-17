"""
Detector layer: turns one video frame into a list of detections and structured scene elements.

    detector = available_detector()
    scene = detector.detect_scene(frame)
    # scene.tables: detected tables with polygon contours
    # scene.chairs: detected chairs for capacity mapping
    # scene.people: detected persons
    # scene.dishes: detected tableware / leftovers

Implementations:
  HogPersonDetector   OpenCV's built-in HOG + SVM people detector. Works completely offline.
  UltralyticsDetector YOLO instance segmentation & detection (tables, chairs, people, tableware).
"""
from dataclasses import dataclass, field
import os

import cv2
import numpy as np

PERSON = "person"
DISHES = "dishes"
TABLE = "table"
CHAIR = "chair"

TABLEWARE_CLASSES = {"cup", "bowl", "bottle", "wine glass", "fork", "knife", "spoon"}


def simplify_polygon(pts_array, epsilon_ratio=0.015):
    """Simplify polygon points using Douglas-Peucker algorithm."""
    if pts_array is None or len(pts_array) < 3:
        return None
    pts = np.array(pts_array, dtype=np.float32).reshape((-1, 1, 2))
    perimeter = cv2.arcLength(pts, True)
    epsilon = max(1.0, epsilon_ratio * perimeter)
    approx = cv2.approxPolyDP(pts, epsilon, True)
    if len(approx) < 3:
        return tuple((float(p[0]), float(p[1])) for p in pts_array)
    return tuple((float(pt[0][0]), float(pt[0][1])) for pt in approx)


def box_to_polygon(box):
    """Convert (x1, y1, x2, y2) bounding box to a 4-point polygon."""
    x1, y1, x2, y2 = box
    return ((float(x1), float(y1)), (float(x2), float(y1)), (float(x2), float(y2)), (float(x1), float(y2)))


@dataclass(frozen=True)
class Detection:
    label: str  # "person", "dishes", "table", "chair"
    box: tuple  # (x1, y1, x2, y2) pixels
    confidence: float
    polygon: tuple = None  # tuple of (x, y) pixels for tables

    @property
    def anchor(self):
        """Point used to decide which table zone the detection belongs to."""
        x1, y1, x2, y2 = self.box
        if self.label == PERSON:
            return ((x1 + x2) / 2, y1 + (y2 - y1) * 0.75)  # roughly hip height
        return ((x1 + x2) / 2, (y1 + y2) / 2)


@dataclass
class SceneDetections:
    tables: list = field(default_factory=list)
    chairs: list = field(default_factory=list)
    people: list = field(default_factory=list)
    dishes: list = field(default_factory=list)

    @property
    def all_occupancy_detections(self):
        """People + dishes for table zone matching (backward compatibility)."""
        return self.people + self.dishes


class HogPersonDetector:
    name = "OpenCV HOG people detector"
    dishes_supported = False
    tables_supported = False

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect_scene(self, frame, min_confidence=0.5):
        people = self.detect(frame, min_confidence=min_confidence)
        return SceneDetections(people=people)

    def detect(self, frame, min_confidence=0.5):
        height, width = frame.shape[:2]
        scale = min(1.0, 640 / max(width, 1))  # HOG is slow on big frames
        small = cv2.resize(frame, (int(width * scale), int(height * scale))) if scale < 1 else frame
        boxes, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        results = []
        for (x, y, w, h), weight in zip(boxes, weights):
            confidence = min(1.0, float(weight) / 2.0)  # SVM margin ~0-2 -> 0-1
            if confidence >= min_confidence:
                results.append(Detection(PERSON, (x / scale, y / scale, (x + w) / scale, (y + h) / scale), confidence))
        return results


class UltralyticsDetector:
    name = "Ultralytics YOLO"
    dishes_supported = True
    tables_supported = True

    def __init__(self, weights=None):
        from ultralytics import YOLO

        if weights is None:
            # Prefer segmentation model if present, otherwise default to yolov8n-seg.pt or yolov8n.pt
            if os.path.exists("yolov8n-seg.pt"):
                weights = "yolov8n-seg.pt"
            elif os.path.exists("yolov8n.pt"):
                weights = "yolov8n.pt"
            else:
                weights = "yolov8n-seg.pt"

        self.model = YOLO(weights)
        self.weights_name = weights
        self.name = f"Ultralytics YOLO ({weights})"

    def detect_scene(self, frame, min_confidence=0.5):
        tables = []
        chairs = []
        people = []
        dishes = []

        results = self.model.predict(frame, verbose=False, conf=float(min_confidence))
        for result in results:
            names = result.names
            has_masks = hasattr(result, "masks") and result.masks is not None

            for i, box in enumerate(result.boxes):
                label_name = names[int(box.cls)]
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                conf = float(box.conf)
                box_coords = (x1, y1, x2, y2)

                if label_name == "dining table" or label_name == TABLE:
                    polygon = None
                    if has_masks and i < len(result.masks.xy):
                        mask_pts = result.masks.xy[i]
                        if len(mask_pts) >= 3:
                            polygon = simplify_polygon(mask_pts)
                    if polygon is None:
                        polygon = box_to_polygon(box_coords)
                    tables.append(Detection(TABLE, box_coords, conf, polygon=polygon))

                elif label_name == "chair" or label_name == CHAIR:
                    chairs.append(Detection(CHAIR, box_coords, conf))

                elif label_name == PERSON:
                    people.append(Detection(PERSON, box_coords, conf))

                elif label_name in TABLEWARE_CLASSES:
                    dishes.append(Detection(DISHES, box_coords, conf))

        return SceneDetections(tables=tables, chairs=chairs, people=people, dishes=dishes)

    def detect(self, frame, min_confidence=0.5):
        scene = self.detect_scene(frame, min_confidence=min_confidence)
        return scene.all_occupancy_detections


def available_detector(prefer_yolo=True):
    """The best detector that can run on this machine."""
    if prefer_yolo:
        try:
            return UltralyticsDetector()
        except Exception:  # noqa: BLE001 - package or weights missing: fall back
            pass
    return HogPersonDetector()
