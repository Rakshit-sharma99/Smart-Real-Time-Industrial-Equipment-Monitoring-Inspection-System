import re

from django import forms
from django.db.models import Q

from core.forms import BootstrapFormMixin

from .metrics import METRICS
from .models import Equipment

DETAIL_FIELDS = ["equipment_code", "name", "equipment_type", "location", "installed_on", "description"]
THRESHOLD_FIELDS = [field for metric in METRICS for field in (metric.warn_field, metric.crit_field)]
CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")


class ThresholdRowsMixin:
    """Lets templates render thresholds as one row per metric."""

    def metric_rows(self):
        return [(metric, self[metric.warn_field], self[metric.crit_field]) for metric in METRICS]


class EquipmentDetailsForm(BootstrapFormMixin, forms.ModelForm):
    """Name, type, location... (used for editing)."""

    class Meta:
        model = Equipment
        fields = DETAIL_FIELDS
        widgets = {
            "installed_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_equipment_code(self):
        code = self.cleaned_data["equipment_code"].strip().upper()
        if not CODE_PATTERN.match(code):
            raise forms.ValidationError("Use capital letters, digits and hyphens only, e.g. EQ-007.")
        return code


class EquipmentCreateForm(ThresholdRowsMixin, EquipmentDetailsForm):
    """Details plus thresholds (pre-filled with the defaults)."""

    class Meta(EquipmentDetailsForm.Meta):
        fields = DETAIL_FIELDS + THRESHOLD_FIELDS


class ThresholdForm(ThresholdRowsMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Equipment
        fields = THRESHOLD_FIELDS


class EquipmentFilterForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(
        attrs={"placeholder": "Code, name or location", "type": "search"}
    ))
    equipment_type = forms.ChoiceField(
        required=False, label="Type", choices=[("", "All types"), *Equipment.EquipmentType.choices]
    )
    location = forms.ChoiceField(required=False, label="Location")
    status = forms.ChoiceField(
        required=False, label="Status", choices=[("", "Any status"), *Equipment.DISPLAY_STATUS_CHOICES]
    )
    active = forms.ChoiceField(
        required=False, label="Active", choices=[("", "All"), ("yes", "Active only"), ("no", "Inactive only")]
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locations = Equipment.objects.order_by("location").values_list("location", flat=True).distinct()
        self.fields["location"].choices = [("", "All locations")] + [(loc, loc) for loc in locations]

    def filter(self, queryset):
        """Apply the submitted filters to an Equipment queryset."""
        if not self.is_valid():
            return queryset
        data = self.cleaned_data
        if data["q"]:
            queryset = queryset.filter(
                Q(equipment_code__icontains=data["q"])
                | Q(name__icontains=data["q"])
                | Q(location__icontains=data["q"])
            )
        if data["equipment_type"]:
            queryset = queryset.filter(equipment_type=data["equipment_type"])
        if data["location"]:
            queryset = queryset.filter(location=data["location"])
        if data["status"]:
            queryset = queryset.with_display_status(data["status"])
        if data["active"] == "yes":
            queryset = queryset.filter(is_active=True)
        elif data["active"] == "no":
            queryset = queryset.filter(is_active=False)
        return queryset
