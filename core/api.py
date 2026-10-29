"""Shared helpers for the JSON API, plus the dashboard summary endpoint."""
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from .services import dashboard_summary


def json_exception_handler(exc, context):
    """
    Give every API error the same shape:  {"error": "...", "details": {...}}

    DRF's default is {"detail": "..."} for most errors and {"field": [...]} for
    validation errors. Normalising makes the front-end JavaScript simpler.
    """
    response = exception_handler(exc, context)
    if response is None:
        return None  # Not an API exception: Django's 500 handling applies.

    data = response.data
    if isinstance(data, dict) and set(data.keys()) == {"detail"}:
        response.data = {"error": str(data["detail"]), "details": {}}
    else:
        response.data = {"error": "Validation failed.", "details": data}
    return response


class DashboardSummaryView(APIView):
    """GET /api/v1/dashboard/summary/ - KPI counts (used by the polling fallback)."""

    def get(self, request):
        return Response(dashboard_summary(request.user))
