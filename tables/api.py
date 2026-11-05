"""GET /api/v1/floor/ - live state of every table (for integrations and the polling fallback)."""
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsRestaurantStaff

from . import services


class FloorApiView(APIView):
    permission_classes = [IsRestaurantStaff]

    def get(self, request):
        tables = services.with_status(services.live_tables(request.restaurant).filter(is_active=True))
        return Response({
            "summary": services.floor_summary(tables),
            "tables": [
                {
                    "table": t.label, "number": t.number, "section": t.section.name, "capacity": t.capacity,
                    "status": t.status, "status_label": t.status_label,
                    "is_occupied": t.is_occupied, "cleanliness": t.cleanliness, "is_blocked": t.is_blocked,
                    "assigned_waiter": (t.assigned_waiter.get_full_name() or t.assigned_waiter.username) if t.assigned_waiter else None,
                    "next_booking_at": t.next_booking_at.isoformat() if t.next_booking_at else None,
                    "status_updated_at": t.status_updated_at.isoformat(),
                }
                for t in tables
            ],
        })
