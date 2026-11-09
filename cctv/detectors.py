"""
Detector layer: turns one video frame into a list of detections.

    detector = available_detector()
    for det in detector.detect(frame):   # Detection(label, box=(x1, y1, x2, y2) in pixels, confidence)

Two implementations:
  HogPersonDetector   OpenCV's built-in HOG + SVM people detector. Works offline with no
                      model download. Detects people only, so it can judge occupancy but
                      NOT dishes (dishes_supported = False).
  UltralyticsDetector YOLO (COCO classes) when the optional `ultralytics` package and a
                      weights file are installed. Detects people AND tableware (cup, bowl,
                      bottle, wine glass, fork, knife, spoon), so it can also flag dishes.

Neither can see whether a surface is wiped clean, so a table is never marked Ready by a
camera (see inspections.services.record_detection).
"""
from dataclasses import dataclass

import cv2

PERSON = "person"
DISHES = "dishes"
TABLEWARE_CLASSES = {"cup", "bowl", "bottle", "wine glass", "fork", "knife", "spoon", "dining table"}


@dataclass(frozen=True)
class Detection:
    label: str  # "person" or "dishes"
    box: tuple  # (x1, y1, x2, y2) pixels
    confidence: float

    @property
    def anchor(self):
        """Point used to decide which table zone the detection belongs to."""
        x1, y1, x2, y2 = self.box
        if self.label == PERSON:
            return ((x1 + x2) / 2, y1 + (y2 - y1) * 0.75)  # roughly hip height: where a seated person "is"
        return ((x1 + x2) / 2, (y1 + y2) / 2)


class HogPersonDetector:
    name = "OpenCV HOG people detector"
    dishes_supported = False

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

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

    def __init__(self, weights="yolov8n.pt"):
        from ultralytics import YOLO  # optional dependency

        self.model = YOLO(weights)
        self.name = f"Ultralytics YOLO ({weights})"

    def detect(self, frame, min_confidence=0.5):
        results = []
        for result in self.model.predict(frame, verbose=False, conf=float(min_confidence)):
            names = result.names
            for box in result.boxes:
                label = names[int(box.cls)]
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0])
                if label == PERSON:
                    results.append(Detection(PERSON, (x1, y1, x2, y2), float(box.conf)))
                elif label in TABLEWARE_CLASSES and label != "dining table":
                    results.append(Detection(DISHES, (x1, y1, x2, y2), float(box.conf)))
        return results


def available_detector(prefer_yolo=True):
    """The best detector that can run on this machine."""
    if prefer_yolo:
        try:
            return UltralyticsDetector()
        except Exception:  # noqa: BLE001 - package or weights missing: fall back
            pass
    return HogPersonDetector()
