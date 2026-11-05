from django import forms
from django.contrib.auth.models import User

from accounts.models import Role
from core.forms import BootstrapFormMixin

from .models import Cleanliness, RestaurantSection, RestaurantTable, TableStatus


def waiters_of(restaurant):
    return User.objects.filter(is_active=True, profile__restaurant=restaurant, profile__role=Role.WAITER).order_by(
        "first_name", "username"
    )


def person(user):
    return user.get_full_name() or user.username


class TableForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = RestaurantTable
        fields = ["number", "section", "capacity", "assigned_waiter"]
        labels = {"number": "Table number", "assigned_waiter": "Responsible waiter"}

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = restaurant
        self.fields["section"].queryset = RestaurantSection.objects.filter(restaurant=restaurant)
        self.fields["assigned_waiter"].queryset = waiters_of(restaurant)
        self.fields["assigned_waiter"].label_from_instance = person
        self.fields["assigned_waiter"].required = False

    def clean_number(self):
        number = self.cleaned_data["number"]
        clash = RestaurantTable.objects.filter(restaurant=self.restaurant, number=number)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f"Table T{number} already exists.")
        return number


class FloorFilterForm(BootstrapFormMixin, forms.Form):
    """Filters for the floor plan: section, status and minimum number of seats."""

    section = forms.ModelChoiceField(queryset=RestaurantSection.objects.none(), required=False, empty_label="All sections")
    status = forms.ChoiceField(required=False, choices=[
        ("", "Any status"), ("available", "Ready now"), *[
            (value, label) for value, label in TableStatus.choices if value != TableStatus.INACTIVE
        ],
    ])
    guests = forms.IntegerField(required=False, min_value=1, max_value=30, label="Guests",
                                widget=forms.NumberInput(attrs={"placeholder": "Any"}))

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["section"].queryset = RestaurantSection.objects.filter(restaurant=restaurant)

    def filter(self, tables):
        """Filter a list of tables that already carry .status."""
        if not self.is_valid():
            return tables
        data = self.cleaned_data
        if data["section"]:
            tables = [t for t in tables if t.section_id == data["section"].pk]
        if data["status"] == "available":
            tables = [t for t in tables if t.status == TableStatus.READY]
        elif data["status"]:
            tables = [t for t in tables if t.status == data["status"]]
        if data["guests"]:
            tables = [t for t in tables if t.capacity >= data["guests"]]
        return tables


class AssignWaiterForm(BootstrapFormMixin, forms.Form):
    waiter = forms.ModelChoiceField(queryset=User.objects.none(), required=False, empty_label="No responsible waiter")

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["waiter"].queryset = waiters_of(restaurant)
        self.fields["waiter"].label_from_instance = person


class OverrideForm(BootstrapFormMixin, forms.Form):
    is_occupied = forms.TypedChoiceField(
        label="Customers seated", choices=[("0", "No"), ("1", "Yes")], coerce=lambda v: v == "1"
    )
    cleanliness = forms.ChoiceField(label="Table condition", choices=Cleanliness.choices)
    reason = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"placeholder": "Why are you overriding?"}))


class BlockForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(max_length=160, widget=forms.TextInput(attrs={"placeholder": "e.g. Wobbly leg, reserved for staff"}))
