"""
JSON API for inspections (mounted under /api/v1/, see config/api_urls.py).

POST /api/v1/detections/ is the integration point for a camera /
computer-vision service. RestaurantSpy does NOT include a vision model: an
external service would detect people and dishes and post its results here.
`python manage.py send_test_detection` is a small client to try the endpoint.
"""
import hmac
import re
import uuid

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsRestaurantStaff
from tables import services as table_services
from tables.models import RestaurantTable

from . import services

DETECTION_AUTH = "detection-token"


class DetectionTokenAuthentication(BaseAuthentication):
    """
    Checks  Authorization: Token <DETECTION_API_TOKEN>.
    Machine-to-machine: there is no session cookie, so CSRF does not apply.
    """

    keyword = "Token"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None  # -> 401 "credentials were not provided"
        keyword, _, token = header.partition(" ")
        token = token.strip()
        if keyword != self.keyword or not token:
            raise AuthenticationFailed("Malformed Authorization header. Expected: Token <value>.")
        expected = settings.DETECTION_API_TOKEN
        # compare_digest takes the same time whichever character differs.
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise AuthenticationFailed("Invalid detection token.")
        return (AnonymousUser(), DETECTION_AUTH)

    def authenticate_header(self, request):
        return self.keyword


class HasDetectionToken(BasePermission):
    def has_permission(self, request, view):
        return request.auth == DETECTION_AUTH


class DetectionSerializer(serializers.Serializer):
    table_id = serializers.UUIDField(help_text="The table's public id (the UUID in its QR code).")
    customers_present = serializers.BooleanField()
    plates_present = serializers.BooleanField()
    glasses_present = serializers.BooleanField()
    leftovers_present = serializers.BooleanField(default=False)
    confidence = serializers.DecimalField(max_digits=4, decimal_places=3, min_value=0, max_value=1, required=False)
    device_id = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")


class DetectionView(APIView):
    authentication_classes = [DetectionTokenAuthentication]
    permission_classes = [HasDetectionToken]
    throttle_scope = "detections"

    def post(self, request):
        serializer = DetectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        table = get_object_or_404(RestaurantTable, public_id=data.pop("table_id"), is_active=True)
        inspection = services.record_detection(table, **data)
        return Response({
            "inspection": inspection.reference,
            "table": table.label,
            "applied": inspection.applied,
            "status": inspection.resulting_status,
        }, status=status.HTTP_201_CREATED)


def find_table(restaurant, query):
    """A table of `restaurant` from a QR UUID, a QR URL, '12' or 'T12'."""
    query = (query or "").strip()
    match = re.search(r"([0-9a-fA-F-]{36})", query)
    if match:
        try:
            return RestaurantTable.objects.filter(restaurant=restaurant, public_id=uuid.UUID(match.group(1))).first()
        except ValueError:
            return None
    number = re.fullmatch(r"[Tt]?(\d{1,4})", query)
    if number:
        return RestaurantTable.objects.filter(restaurant=restaurant, number=int(number.group(1))).first()
    return None


class TableLookupView(APIView):
    """GET /api/v1/tables/lookup/?q=<T12, 12 or QR UUID> - used by the scanner page."""

    permission_classes = [IsRestaurantStaff]

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response({"error": "Give a table number or QR code in ?q=", "details": {}},
                            status=status.HTTP_400_BAD_REQUEST)
        table = find_table(request.restaurant, query)
        if table is None:
            return Response({"error": "Unknown table", "details": {"q": query}}, status=status.HTTP_404_NOT_FOUND)
        can_inspect = table_services.can_inspect(request.user, table)
        return Response({
            "table": table.label,
            "capacity": table.capacity,
            "url": table.get_absolute_url(),
            "inspect_url": reverse("inspections:inspect", args=[table.pk]) if can_inspect else None,
        })
