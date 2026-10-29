from django import forms
from django.db.models import Q

from core.forms import BootstrapFormMixin
from equipment.metrics import METRIC_CHOICES
from equipment.models import Equipment

from .models import Alert, SimulatorSettings


class SimulatorSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SimulatorSettings
        fields = ["interval_seconds", "noise_pct"]


class AlertFilterForm(BootstrapFormMixin, forms.Form):
    """Filters shared by the alerts page and GET /api/v1/alerts/."""

    STATUS_CHOICES = [
        ("open", "Open (active + acknowledged)"),
        ("all", "All statuses"),
        *Alert.Status.choices,
    ]

    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)
    severity = forms.ChoiceField(choices=[("", "Any severity"), *Alert.Severity.choices], required=False)
    metric = forms.ChoiceField(choices=[("", "Any metric"), *METRIC_CHOICES], required=False)
    equipment = forms.ChoiceField(required=False, label="Equipment")
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(
        attrs={"type": "search", "placeholder": "Code, name or text"}
    ))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        codes = Equipment.objects.values_list("equipment_code", flat=True)
        self.fields["equipment"].choices = [("", "All equipment")] + [(c, c) for c in codes]

    def filter(self, queryset):
        if not self.is_valid():
            return queryset.open()
        data = self.cleaned_data
        status = data["status"] or "open"
        if status == "open":
            queryset = queryset.open()
        elif status != "all":
            queryset = queryset.filter(status=status)
        if data["severity"]:
            queryset = queryset.filter(severity=data["severity"])
        if data["metric"]:
            queryset = queryset.filter(metric=data["metric"])
        if data["equipment"]:
            queryset = queryset.filter(equipment__equipment_code=data["equipment"])
        if data["q"]:
            queryset = queryset.filter(
                Q(equipment__equipment_code__icontains=data["q"])
                | Q(equipment__name__icontains=data["q"])
                | Q(description__icontains=data["q"])
            )
        return queryset


class ResolveAlertForm(BootstrapFormMixin, forms.Form):
    resolution_notes = forms.CharField(
        label="Resolution notes",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "What was found and what was done?"}),
        help_text="Required. Stored with the alert and shown in reports.",
    )
