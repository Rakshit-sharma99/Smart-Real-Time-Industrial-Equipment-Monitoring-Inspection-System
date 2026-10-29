from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.forms import inlineformset_factory, modelformset_factory
from django.utils import timezone

from accounts.models import Role
from core.forms import BootstrapFormMixin
from equipment.models import Equipment

from . import services
from .models import ChecklistItem, Defect, Inspection, InspectionResponse, InspectionTemplate

Outcome = InspectionResponse.Outcome


class ResponseForm(BootstrapFormMixin, forms.ModelForm):
    """One checklist line on the 'perform inspection' page."""

    outcome = forms.ChoiceField(choices=Outcome.choices, required=False, widget=forms.RadioSelect)
    photo_upload = forms.FileField(
        required=False,
        label="Photo",
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}),
    )

    class Meta:
        model = InspectionResponse
        fields = ["outcome", "remarks"]
        widgets = {"remarks": forms.Textarea(attrs={"rows": 2, "placeholder": "Remarks (required for Fail or Needs attention)"})}

    def clean_photo_upload(self):
        uploaded = self.cleaned_data.get("photo_upload")
        if not uploaded:
            return None
        return services.process_photo(uploaded)  # raises ValidationError for bad files

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("outcome") in services.REMARKS_REQUIRED_FOR and not (cleaned.get("remarks") or "").strip():
            self.add_error("remarks", "Remarks are required for Fail and Needs attention.")
        return cleaned

    def save(self, commit=True):
        response = super().save(commit=False)
        clean_photo = self.cleaned_data.get("photo_upload")
        if clean_photo is not None:
            if response.photo:
                response.photo.delete(save=False)  # replace: remove the old file
            response.photo.save("photo.jpg", clean_photo, save=False)
        if commit:
            response.save()
        return response


ResponseFormSet = modelformset_factory(InspectionResponse, form=ResponseForm, extra=0, can_delete=False)


class InspectionRemarksForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Inspection
        fields = ["remarks"]
        widgets = {"remarks": forms.Textarea(attrs={"rows": 3, "placeholder": "Anything else the next person should know"})}


def assignable_users():
    """Active inspectors (and admins, who may also be assigned)."""
    return User.objects.filter(is_active=True).filter(
        Q(profile__role__in=[Role.INSPECTOR, Role.ADMIN]) | Q(is_superuser=True)
    ).order_by("username")


class AssignInspectionForm(BootstrapFormMixin, forms.Form):
    equipment = forms.ModelChoiceField(queryset=Equipment.objects.active())
    template = forms.ModelChoiceField(
        queryset=InspectionTemplate.objects.active(), label="Checklist template"
    )
    inspector = forms.ModelChoiceField(queryset=User.objects.none(), label="Assign to")
    due_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["inspector"].queryset = assignable_users()
        self.fields["inspector"].label_from_instance = lambda u: f"{u.get_full_name() or u.username} ({u.username})"
        self.fields["template"].label_from_instance = (
            lambda t: f"{t.name} ({t.get_equipment_type_display() or 'general'})"
        )

    def clean_due_date(self):
        due = self.cleaned_data.get("due_date")
        if due and due < timezone.localdate():
            raise forms.ValidationError("The due date cannot be in the past.")
        return due


class InspectionFilterForm(BootstrapFormMixin, forms.Form):
    STATUS_CHOICES = [("open", "Open (assigned + in progress)"), ("all", "All statuses"), *Inspection.Status.choices]

    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)
    result = forms.ChoiceField(choices=[("", "Any result"), *Inspection.Result.choices], required=False)
    equipment = forms.ChoiceField(required=False)

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
        if data["result"]:
            queryset = queryset.filter(overall_result=data["result"])
        if data["equipment"]:
            queryset = queryset.filter(equipment__equipment_code=data["equipment"])
        return queryset


class DefectFilterForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(
        choices=[("unresolved", "Unresolved (open + in maintenance)"), ("all", "All statuses"), *Defect.Status.choices],
        required=False,
    )
    severity = forms.ChoiceField(choices=[("", "Any severity"), *Defect.Severity.choices], required=False)

    def filter(self, queryset):
        if not self.is_valid():
            return queryset.unresolved()
        status = self.cleaned_data["status"] or "unresolved"
        if status == "unresolved":
            queryset = queryset.unresolved()
        elif status != "all":
            queryset = queryset.filter(status=status)
        if self.cleaned_data["severity"]:
            queryset = queryset.filter(severity=self.cleaned_data["severity"])
        return queryset


class TemplateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = InspectionTemplate
        fields = ["name", "equipment_type", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class ChecklistItemForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ChecklistItem
        fields = ["order", "text"]
        widgets = {"order": forms.NumberInput(attrs={"min": 0, "style": "max-width: 90px"})}


ChecklistItemFormSet = inlineformset_factory(
    InspectionTemplate,
    ChecklistItem,
    form=ChecklistItemForm,
    extra=3,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
