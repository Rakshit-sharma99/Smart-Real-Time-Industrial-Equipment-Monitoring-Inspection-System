from django import forms

from core.forms import BootstrapFormMixin
from tables.models import Cleanliness, TableStatus

from . import services
from .models import TableInspection
from .photos import process_photo

YES_NO = [("1", "Yes"), ("0", "No")]
MAX_PHOTOS = 3

# (field name, question) in the order staff answer them.
QUESTIONS = [
    ("customers_present", "Are customers sitting at the table?"),
    ("plates_present", "Are plates on the table?"),
    ("glasses_present", "Are glasses on the table?"),
    ("leftovers_present", "Is there leftover food?"),
    ("table_clean", "Is the table surface clean?"),
    ("chairs_arranged", "Are the chairs arranged?"),
    ("ready_for_next", "Is the table ready for the next customers?"),
]


def yes_no(value):
    """Python bool -> the radio value used by the forms."""
    return "1" if value else "0"


def yes_no_field(label):
    return forms.TypedChoiceField(label=label, choices=YES_NO, coerce=lambda v: v == "1", widget=forms.RadioSelect)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiplePhotoField(forms.FileField):
    """Up to MAX_PHOTOS images, each validated and cleaned by process_photo()."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp", "capture": "environment"}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > MAX_PHOTOS:
            raise forms.ValidationError(f"Attach at most {MAX_PHOTOS} photos.")
        return [process_photo(super(MultiplePhotoField, self).clean(f, initial)) for f in files]


class TableCheckForm(BootstrapFormMixin, forms.Form):
    """The seven questions about a table. Used for inspections and for completing tasks."""

    customers_present = yes_no_field("Customers seated")
    plates_present = yes_no_field("Plates on the table")
    glasses_present = yes_no_field("Glasses on the table")
    leftovers_present = yes_no_field("Leftover food")
    table_clean = yes_no_field("Table surface clean")
    chairs_arranged = yes_no_field("Chairs arranged")
    ready_for_next = yes_no_field("Ready for the next customers")
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Anything worth noting"}))

    def questions(self):
        return [(self[name], text) for name, text in QUESTIONS]

    def answers(self):
        return {name: self.cleaned_data[name] for name, _ in QUESTIONS}

    def clean(self):
        cleaned = super().clean()
        if all(name in cleaned for name, _ in QUESTIONS):
            for problem in services.contradictions({name: cleaned[name] for name, _ in QUESTIONS}):
                self.add_error(None, problem)
        return cleaned


class InspectionForm(TableCheckForm):
    photos = MultiplePhotoField(label="Photos (optional)", help_text="JPEG, PNG or WebP, max 5 MB each, up to 3.")


def initial_from_table(table):
    """Pre-fill the answers with what the system currently believes, so staff only change what differs."""
    clean = table.cleanliness == Cleanliness.CLEAN
    dishes = table.cleanliness == Cleanliness.NEEDS_CLEARING
    return {
        "customers_present": yes_no(table.is_occupied),
        "plates_present": yes_no(dishes),
        "glasses_present": yes_no(dishes),
        "leftovers_present": "0",
        "table_clean": yes_no(clean),
        "chairs_arranged": yes_no(clean),
        "ready_for_next": yes_no(getattr(table, "status", None) in (TableStatus.READY, TableStatus.RESERVED)),
    }


# Pre-filled answers when a waiter completes a task: the table should now be clear and ready.
AFTER_CLEANING = {
    "customers_present": "0", "plates_present": "0", "glasses_present": "0", "leftovers_present": "0",
    "table_clean": "1", "chairs_arranged": "1", "ready_for_next": "1",
}


class InspectionFilterForm(BootstrapFormMixin, forms.Form):
    table = forms.IntegerField(required=False, min_value=1, label="Table number")
    source = forms.ChoiceField(required=False, choices=[("", "Any source"), *TableInspection.Source.choices])

    def filter(self, inspections):
        if not self.is_valid():
            return inspections
        if self.cleaned_data["table"]:
            inspections = inspections.filter(table__number=self.cleaned_data["table"])
        if self.cleaned_data["source"]:
            inspections = inspections.filter(source=self.cleaned_data["source"])
        return inspections
