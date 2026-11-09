"""Worker-facing camera endpoints (token auth; mounted under /api/v1/cameras/)."""
import base64
import binascii

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from inspections.api import DetectionTokenAuthentication, HasDetectionToken

from . import services
from .models import Camera


class ZoneResultSerializer(serializers.Serializer):
    people = serializers.IntegerField(min_value=0)
    dishes = serializers.IntegerField(min_value=0)
    confidence = serializers.FloatField(min_value=0, max_value=1)
    occupied = serializers.BooleanField()
    dishes_present = serializers.BooleanField(allow_null=True)


class ZoneChangeSerializer(serializers.Serializer):
    zone = serializers.IntegerField()
    occupied = serializers.BooleanField()
    dishes = serializers.BooleanField(allow_null=True, required=False, default=None)
    confidence = serializers.FloatField(min_value=0, max_value=1, allow_null=True, required=False, default=None)


class FrameSerializer(serializers.Serializer):
    frame_jpeg = serializers.CharField(allow_blank=True)  # base64-encoded JPEG
    width = serializers.IntegerField(min_value=1, max_value=8000)
    height = serializers.IntegerField(min_value=1, max_value=8000)
    detector = serializers.CharField(max_length=80)
    detects_dishes = serializers.BooleanField()
    zones = serializers.DictField(child=ZoneResultSerializer(), required=False, default=dict)
    changes = ZoneChangeSerializer(many=True, required=False, default=list)
    detections = serializers.ListField(required=False, default=list)


class CameraFrameView(APIView):
    """POST /api/v1/cameras/<id>/frame/ - the worker's latest frame, zone results and confirmed changes."""

    authentication_classes = [DetectionTokenAuthentication]
    permission_classes = [HasDetectionToken]
    throttle_scope = "detections"

    def post(self, request, pk):
        camera = get_object_or_404(Camera, pk=pk, is_active=True)
        serializer = FrameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            jpeg = base64.b64decode(data["frame_jpeg"], validate=True) if data["frame_jpeg"] else b""
        except (ValueError, binascii.Error):
            return Response({"error": "frame_jpeg is not valid base64.", "details": {}}, status=status.HTTP_400_BAD_REQUEST)
        if jpeg[:2] != b"\xff\xd8":
            return Response({"error": "The frame is not a JPEG.", "details": {}}, status=status.HTTP_400_BAD_REQUEST)
        services.store_frame(camera, jpeg, data["width"], data["height"])
        services.set_detector(camera, data["detector"], data["detects_dishes"])
        services.store_results(camera, {str(k): v for k, v in data["zones"].items()})
        applied = services.apply_zone_changes(camera, data["changes"])
        return Response({"stored": True, "applied": applied}, status=status.HTTP_201_CREATED)


class CameraStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[Camera.Status.OFFLINE])
    error = serializers.CharField(max_length=300, allow_blank=True, default="")


class CameraStatusView(APIView):
    """POST /api/v1/cameras/<id>/status/ - the worker reports a lost stream. Tables are not touched."""

    authentication_classes = [DetectionTokenAuthentication]
    permission_classes = [HasDetectionToken]

    def post(self, request, pk):
        camera = get_object_or_404(Camera, pk=pk)
        serializer = CameraStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.mark_offline(camera, serializer.validated_data["error"])
        return Response({"status": Camera.Status.OFFLINE})
