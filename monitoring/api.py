"""JSON API endpoints for monitoring (mounted under /api/v1/, see config/api_urls.py)."""
import hmac

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from equipment.models import Equipment

from . import services

SIMULATOR_AUTH = "simulator-token"
MAX_BATCH = 100


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
