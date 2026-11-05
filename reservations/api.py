"""JSON API for bookings (mounted under /api/v1/, see config/api_urls.py)."""
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsBookingStaff
from tables.models import RestaurantSection, RestaurantTable

from . import services


class AvailabilityQuerySerializer(serializers.Serializer):
    start_at = serializers.DateTimeField()
    guests = serializers.IntegerField(min_value=1, max_value=30)
    duration = serializers.IntegerField(min_value=15, max_value=360, required=False)
    section = serializers.IntegerField(required=False)


class AvailabilityView(APIView):
    """GET /api/v1/reservations/availability/?start_at=2026-11-05T19:30&guests=4[&duration=90&section=2]"""

    permission_classes = [IsBookingStaff]

    def get(self, request):
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        section = None
        if data.get("section"):
            section = get_object_or_404(RestaurantSection, pk=data["section"], restaurant=request.restaurant)
        end = services.end_for(request.restaurant, data["start_at"], data.get("duration"))
        tables = services.available_tables(request.restaurant, start=data["start_at"], end=end,
                                           party_size=data["guests"], section=section)
        return Response({
            "start_at": data["start_at"].isoformat(),
            "end_at": end.isoformat(),
            "tables": [{"table": t.label, "number": t.number, "capacity": t.capacity, "section": t.section.name,
                        "status_now": t.status} for t in tables],
        })


class ReservationCreateSerializer(serializers.Serializer):
    table = serializers.IntegerField(help_text="Table number, e.g. 12")
    customer_name = serializers.CharField(max_length=100)
    phone = serializers.CharField(max_length=20)
    party_size = serializers.IntegerField(min_value=1, max_value=30)
    start_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(min_value=15, max_value=360, required=False)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ReservationCreateView(APIView):
    """POST /api/v1/reservations/ - book a table. Rate limited; conflicts are refused with 409."""

    permission_classes = [IsBookingStaff]
    throttle_scope = "reservations"

    def post(self, request):
        serializer = ReservationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        table = get_object_or_404(RestaurantTable, restaurant=request.restaurant, number=data["table"])
        try:
            reservation = services.create_reservation(
                restaurant=request.restaurant, table=table, customer_name=data["customer_name"], phone=data["phone"],
                party_size=data["party_size"], start_at=data["start_at"], duration_minutes=data.get("duration_minutes"),
                remarks=data["remarks"], created_by=request.user,
            )
        except services.BookingConflict as exc:
            return Response({"error": "The table is already booked for that time.", "details": exc.message_dict},
                            status=status.HTTP_409_CONFLICT)
        except ValidationError as exc:
            details = exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
            return Response({"error": "Booking refused.", "details": details}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "reference": reservation.reference, "table": table.label, "status": reservation.status,
            "start_at": timezone.localtime(reservation.start_at).isoformat(),
            "end_at": timezone.localtime(reservation.end_at).isoformat(),
        }, status=status.HTTP_201_CREATED)
