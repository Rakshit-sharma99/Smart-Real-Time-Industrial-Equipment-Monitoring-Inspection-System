"""JSON API endpoints for monitoring (mounted under /api/v1/, see config/api_urls.py)."""
import hmac

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsAdminRole
from equipment.metrics import METRICS
from equipment.models import Equipment

from . import services
from .forms import AlertFilterForm
from .models import Alert

SIMULATOR_AUTH = "simulator-token"
MAX_BATCH = 100
DEFAULT_READINGS = 60
MAX_READINGS = 500


def equipment_status_item(equipment, reading):
    return {
        "code": equipment.equipment_code,
        "name": equipment.name,
        "location": equipment.location,
        "status": equipment.display_status,
        "status_label": equipment.display_status_label,
        "last_reading_at": equipment.last_reading_at.isoformat() if equipment.last_reading_at else None,
        "values": services.reading_values(reading),
    }


def thresholds_payload(equipment):
    return {
        metric.key: {"label": metric.label, "unit": metric.unit, "warn": str(warn), "crit": str(crit)}
        for metric, warn, crit in equipment.threshold_rows()
    }


class EquipmentStatusView(APIView):
    """GET /api/v1/equipment/status/ - every active machine with its status and latest values."""

    def get(self, request):
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(Equipment.objects.active(), request, view=self)
        latest = services.latest_readings(page)
        items = [equipment_status_item(equipment, latest.get(equipment.pk)) for equipment in page]
        return paginator.get_paginated_response(items)


class EquipmentReadingsView(APIView):
    """GET /api/v1/equipment/<code>/readings/?limit=60 - recent readings, oldest first (for charts)."""

    def get(self, request, code):
        equipment = get_object_or_404(Equipment, equipment_code=code)
        try:
            limit = int(request.query_params.get("limit", DEFAULT_READINGS))
        except ValueError:
            limit = DEFAULT_READINGS
        limit = max(1, min(limit, MAX_READINGS))
        readings = list(equipment.readings.order_by("-recorded_at", "-id")[:limit])
        readings.reverse()
        return Response({
            "equipment": {
                "code": equipment.equipment_code,
                "name": equipment.name,
                "status": equipment.display_status,
                "status_label": equipment.display_status_label,
            },
            "thresholds": thresholds_payload(equipment),
            "readings": [
                {"recorded_at": r.recorded_at.isoformat(), "source": r.source,
                 **{metric.key: str(r.value_for(metric)) for metric in METRICS}}
                for r in readings
            ],
        })


class SimulatorTokenAuthentication(BaseAuthentication):
    """
    Checks the header  Authorization: Token <SIMULATOR_TOKEN>.

    This is machine-to-machine auth, not a user login: there is no session
    cookie, so CSRF does not apply and the ingest view is CSRF-exempt.
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
        expected = settings.SIMULATOR_TOKEN
        # compare_digest takes the same time whether the first or last character differs.
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise AuthenticationFailed("Invalid simulator token.")
        return (AnonymousUser(), SIMULATOR_AUTH)

    def authenticate_header(self, request):
        return self.keyword


class IsSimulator(BasePermission):
    def has_permission(self, request, view):
        return request.auth == SIMULATOR_AUTH


class ReadingInSerializer(serializers.Serializer):
    equipment_code = serializers.CharField(max_length=20)
    temperature_c = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)
    vibration_mm_s = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)
    pressure_bar = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0)


class IngestSerializer(serializers.Serializer):
    readings = ReadingInSerializer(many=True, allow_empty=False, max_length=MAX_BATCH)


class IngestView(APIView):
    """POST /api/v1/ingest/  - a batch of simulated readings from the simulator."""

    authentication_classes = [SimulatorTokenAuthentication]
    permission_classes = [IsSimulator]

    def post(self, request):
        serializer = IngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["readings"]

        codes = {item["equipment_code"] for item in items}
        equipment_by_code = Equipment.objects.in_bulk(codes, field_name="equipment_code")
        accepted, rejected = 0, []
        for item in items:
            equipment = equipment_by_code.get(item["equipment_code"])
            if equipment is None:
                rejected.append({"equipment_code": item["equipment_code"], "error": "Unknown equipment code."})
                continue
            try:
                services.process_reading(
                    equipment,
                    temperature_c=item["temperature_c"],
                    vibration_mm_s=item["vibration_mm_s"],
                    pressure_bar=item["pressure_bar"],
                )
            except ValidationError as exc:
                rejected.append({"equipment_code": item["equipment_code"], "error": " ".join(exc.messages)})
                continue
            accepted += 1

        services.record_ingest()
        if not accepted:
            return Response(
                {"error": "No readings were accepted.", "details": {"rejected": rejected}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"accepted": accepted, "rejected": rejected}, status=status.HTTP_201_CREATED)


# --- Alerts ------------------------------------------------------------------------


def alert_item(alert):
    return {
        "id": alert.pk,
        "reference": alert.reference,
        "equipment": {"code": alert.equipment.equipment_code, "name": alert.equipment.name},
        "metric": alert.metric,
        "severity": alert.severity,
        "status": alert.status,
        "first_value": str(alert.first_value),
        "last_value": str(alert.last_value),
        "peak_value": str(alert.peak_value),
        "threshold_value": str(alert.threshold_value),
        "description": alert.description,
        "created_at": alert.created_at.isoformat(),
        "last_seen_at": alert.last_seen_at.isoformat(),
        "escalated_at": alert.escalated_at.isoformat() if alert.escalated_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "resolution_notes": alert.resolution_notes,
    }


class AlertListApiView(APIView):
    """GET /api/v1/alerts/?status=open|all|ACTIVE|...&severity=&metric=&equipment=EQ-001"""

    def get(self, request):
        form = AlertFilterForm(request.query_params or {"status": "open"})
        alerts = form.filter(Alert.objects.select_related("equipment"))
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(alerts, request, view=self)
        return paginator.get_paginated_response([alert_item(alert) for alert in page])


class AlertAcknowledgeApiView(APIView):
    """POST /api/v1/alerts/<id>/acknowledge/ - any logged-in role."""

    def post(self, request, pk):
        alert = get_object_or_404(Alert, pk=pk)
        try:
            alert = services.acknowledge_alert(alert, request.user)
        except services.AlertActionError as exc:
            return Response({"error": str(exc), "details": {}}, status=status.HTTP_409_CONFLICT)
        return Response(alert_item(alert))


class AlertResolveApiView(APIView):
    """POST /api/v1/alerts/<id>/resolve/  {"resolution_notes": "..."} - admins only."""

    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        alert = get_object_or_404(Alert, pk=pk)
        try:
            alert = services.resolve_alert(alert, request.user, request.data.get("resolution_notes", ""))
        except ValidationError as exc:
            return Response(
                {"error": "Resolution notes are required.", "details": exc.message_dict},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except services.AlertActionError as exc:
            return Response({"error": str(exc), "details": {}}, status=status.HTTP_409_CONFLICT)
        return Response(alert_item(alert))
