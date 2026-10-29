"""
Report pages and PDF downloads.

Authorization runs BEFORE any PDF is generated, using the same visible_to(user)
querysets as the rest of the app: asking for someone else's inspection by
changing the id in the URL gives a 404.
"""
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from equipment.models import Equipment
from inspections.models import Inspection

from . import services


def pdf_response(pdf_bytes, filename, inline=False):
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


def reports_home(request):
    inspections = (
        Inspection.objects.visible_to(request.user)
        .filter(status=Inspection.Status.SUBMITTED)
        .select_related("equipment", "inspector")
        .order_by("-submitted_at")[:20]
    )
    context = {
        "inspections": inspections,
        "equipment_list": Equipment.objects.all(),
        "default_days": services.DEFAULT_DAYS,
        "max_days": services.MAX_DAYS,
        "page_title": "Reports",
    }
    return render(request, "reports/home.html", context)


def inspection_report(request, pk):
    inspection = get_object_or_404(
        Inspection.objects.visible_to(request.user).select_related("equipment", "template", "inspector"), pk=pk
    )
    pdf = services.build_inspection_report(inspection)
    day = timezone.localtime(inspection.submitted_at or timezone.now()).date().isoformat()
    filename = services.safe_filename("inspection", inspection.equipment.equipment_code, day)
    return pdf_response(pdf, filename, inline=request.GET.get("inline") == "1")


def health_report(request, code):
    equipment = get_object_or_404(Equipment, equipment_code=code)
    days = services.clamp_days(request.GET.get("days", services.DEFAULT_DAYS))
    pdf = services.build_health_report(equipment, request.user, days=days)
    filename = services.safe_filename("health", equipment.equipment_code, timezone.localdate().isoformat())
    return pdf_response(pdf, filename, inline=request.GET.get("inline") == "1")
