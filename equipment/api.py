"""Equipment JSON API (mounted under /api/v1/, see config/api_urls.py)."""
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Equipment


def find_equipment(query):
    """Look a machine up by its public UUID or by its equipment code (case-insensitive)."""
    query = (query or "").strip()
    try:
        public_id = uuid.UUID(query)
    except ValueError:
        return Equipment.objects.filter(equipment_code__iexact=query).first()
    return Equipment.objects.filter(public_id=public_id).first()


class EquipmentLookupView(APIView):
    """
    GET /api/v1/equipment/lookup/?q=<equipment code or UUID>

    Used by the QR scanner page after it has decoded a QR code (or the user
    typed a code). 404 when nothing matches.
    """

    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response(
                {"error": "Give an equipment code or UUID in ?q=", "details": {}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        equipment = find_equipment(query)
        if equipment is None:
            return Response({"error": "Unknown equipment", "details": {"q": query}}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "code": equipment.equipment_code,
            "name": equipment.name,
            "location": equipment.location,
            "is_active": equipment.is_active,
            "status": equipment.display_status,
            "url": equipment.get_absolute_url(),
        })
