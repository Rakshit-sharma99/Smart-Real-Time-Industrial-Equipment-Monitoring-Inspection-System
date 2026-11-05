from datetime import datetime, timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.forms import BootstrapFormMixin
from tables.models import RestaurantSection, RestaurantTable

from .models import Reservation, phone_validator


def local_datetime(day, time_of_day):
    """Date + time typed at the desk -> aware datetime in the restaurant's time zone."""
    return timezone.make_aware(datetime.combine(day, time_of_day))


class BookingSearchForm(BootstrapFormMixin, forms.Form):
    """Step 1 at the desk: when, how many guests, where."""

    date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time", "step": 900}))
    guests = forms.IntegerField(min_value=1, max_value=30)
    section = forms.ModelChoiceField(queryset=RestaurantSection.objects.none(), required=False, empty_label="Any section")
    duration = forms.IntegerField(required=False, min_value=15, max_value=360, label="Minutes",
                                  widget=forms.NumberInput(attrs={"placeholder": "Default"}))

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.restaurant = restaurant
        self.fields["section"].queryset = RestaurantSection.objects.filter(restaurant=restaurant)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("date") and cleaned.get("time"):
            cleaned["start_at"] = local_datetime(cleaned["date"], cleaned["time"])
            if cleaned["start_at"] < timezone.now() - timedelta(minutes=5):
                self.add_error("time", "This time has already passed.")
        return cleaned


class BookingForm(BookingSearchForm):
    """Step 2: the chosen table and the customer's details."""

    table = forms.ModelChoiceField(queryset=RestaurantTable.objects.none(), widget=forms.RadioSelect,
                                   error_messages={"required": "Choose one of the suggested tables."})
    customer_name = forms.CharField(max_length=100, label="Customer name")
    phone = forms.CharField(max_length=20, validators=[phone_validator])
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Birthday, high chair, allergies..."}))

    def __init__(self, *args, restaurant=None, **kwargs):
        super().__init__(*args, restaurant=restaurant, **kwargs)
        self.fields["table"].queryset = RestaurantTable.objects.filter(restaurant=restaurant, is_active=True)


class WalkInForm(BootstrapFormMixin, forms.Form):
    table = forms.ModelChoiceField(queryset=RestaurantTable.objects.none(), empty_label="Choose a Ready table")
    guests = forms.IntegerField(min_value=1, max_value=30)
    customer_name = forms.CharField(max_length=100, required=False, label="Name (optional)")

    def __init__(self, *args, restaurant=None, ready_tables=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["table"].queryset = RestaurantTable.objects.filter(restaurant=restaurant, is_active=True)
        if ready_tables is not None:
            self.fields["table"].choices = [("", "Choose a Ready table")] + [
                (t.pk, f"{t.label} · {t.capacity} seats · {t.section.name}") for t in ready_tables
            ]


class ReservationFilterForm(BootstrapFormMixin, forms.Form):
    STATUS_CHOICES = [("upcoming", "Upcoming & seated"), ("all", "All statuses"), *Reservation.Status.choices]

    date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)
    q = forms.CharField(required=False, label="Search", widget=forms.TextInput(attrs={"type": "search", "placeholder": "Name, phone or RS-0001"}))

    def filter(self, reservations):
        if not self.is_valid():
            return reservations.active()
        data = self.cleaned_data
        status = data["status"] or "upcoming"
        if status == "upcoming":
            reservations = reservations.active().filter(end_at__gt=timezone.now())
        elif status != "all":
            reservations = reservations.filter(status=status)
        if data["date"]:
            reservations = reservations.filter(start_at__date=data["date"])
        if data["q"]:
            query = data["q"].strip()
            reservations = reservations.filter(
                Q(customer_name__icontains=query) | Q(phone__icontains=query) | Q(reference__iexact=query)
            )
        return reservations
