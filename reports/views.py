"""Reports pages and PDF downloads (managers only; checked before anything is generated)."""
from datetime import timedelta

from django import forms
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.forms import BootstrapFormMixin
from core.permissions import manager_required
from tables.models import RestaurantTable

from . import analytics, services


class PeriodForm(BootstrapFormMixin, forms.Form):
    start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end:
            if end < start:
                self.add_error("end", "The end date is before the start date.")
            elif (end - start).days + 1 > analytics.MAX_DAYS:
                self.add_error("end", f"Choose at most {analytics.MAX_DAYS} days.")
        return cleaned


def chosen_period(request):
    """Period from the query string (default: the last 7 days including today)."""
    today = timezone.localdate()
    form = PeriodForm(request.GET or None, initial={"start": today - timedelta(days=6), "end": today})
    if form.is_bound and form.is_valid():
        return form, form.cleaned_data["start"], form.cleaned_data["end"]
    return form, today - timedelta(days=6), today


def pdf_response(pdf_bytes, filename):
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    return response


@manager_required
def reports_home(request):
    form, start, end = chosen_period(request)
    report = analytics.operations_report(request.restaurant, start, end)
    return render(request, "reports/home.html", {
        "form": form, "report": report, "week": analytics.weekly_totals(report),
        "peak_labels": [f"{hour:02d}:00" for hour, _ in report.peak_hours],
        "peak_values": [value for _, value in report.peak_hours],
        "tables": RestaurantTable.objects.filter(restaurant=request.restaurant).order_by("number"),
        "query": request.GET.urlencode(),
        "page_title": "Reports",
    })


@manager_required
def operations_pdf(request):
    _, start, end = chosen_period(request)
    report = analytics.operations_report(request.restaurant, start, end)
    pdf = services.build_operations_pdf(request.restaurant, report)
    return pdf_response(pdf, services.safe_filename("operations", request.restaurant.slug, start, end))


@manager_required
def table_history_pdf(request, pk):
    table = get_object_or_404(RestaurantTable.objects.select_related("restaurant", "section"),
                              pk=pk, restaurant=request.restaurant)
    pdf = services.build_table_history_pdf(table)
    return pdf_response(pdf, services.safe_filename("table", table.label, timezone.localdate()))
