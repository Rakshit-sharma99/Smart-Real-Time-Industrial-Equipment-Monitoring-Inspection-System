from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from tables.models import Restaurant, RestaurantTable

phone_validator = RegexValidator(r"^\+?[0-9][0-9 -]{5,18}$", "Enter a phone number (digits, spaces, dashes, optional +).")


class ReservationQuerySet(models.QuerySet):
    def for_restaurant(self, restaurant):
        return self.filter(restaurant=restaurant)

    def active(self):
        """Bookings that still claim their time slot on the table."""
        return self.filter(status__in=Reservation.ACTIVE_STATUSES)

    def overlapping(self, start, end):
        """Time slots that overlap [start, end). Touching slots (one ends as the next starts) do not overlap."""
        return self.filter(start_at__lt=end, end_at__gt=start)


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        CONFIRMED = "CONFIRMED", "Confirmed"
        ARRIVED = "ARRIVED", "Arrived"
        SEATED = "SEATED", "Seated"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No show"

    class Source(models.TextChoices):
        BOOKING = "BOOKING", "Booking"
        WALK_IN = "WALK_IN", "Walk-in"

    # Still holding the table's time slot (used for conflict checks).
    ACTIVE_STATUSES = (Status.PENDING, Status.CONFIRMED, Status.ARRIVED, Status.SEATED)
    # Guests not seated yet: the table is shown as Reserved during the hold window.
    WAITING_STATUSES = (Status.PENDING, Status.CONFIRMED, Status.ARRIVED)

    reference = models.CharField(max_length=12, unique=True, null=True, blank=True, editable=False)
    restaurant = models.ForeignKey(Restaurant, on_delete=models.PROTECT, related_name="reservations")
    table = models.ForeignKey(RestaurantTable, on_delete=models.PROTECT, related_name="reservations")
    customer_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True, validators=[phone_validator])
    party_size = models.PositiveSmallIntegerField("Guests", validators=[MinValueValidator(1)])
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMED)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.BOOKING)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    seated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    objects = ReservationQuerySet.as_manager()

    class Meta:
        ordering = ["start_at", "id"]
        indexes = [
            models.Index(fields=["table", "start_at"], name="reservation_table_start"),
            models.Index(fields=["restaurant", "start_at"], name="reservation_restaurant_start"),
            models.Index(fields=["restaurant", "status"], name="reservation_restaurant_status"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(end_at__gt=F("start_at")), name="reservation_ends_after_start"),
            models.CheckConstraint(condition=Q(party_size__gte=1), name="reservation_has_guests"),
        ]

    def __str__(self):
        return f"{self.reference} {self.customer_name} ({self.party_size}) {self.table}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"RS-{self.pk:04d}"
            Reservation.objects.filter(pk=self.pk).update(reference=self.reference)

    @property
    def is_waiting(self):
        return self.status in self.WAITING_STATUSES

    @property
    def is_late(self):
        """Not arrived although the booking time has passed."""
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED) and self.start_at < timezone.now()

    @property
    def waiting_minutes(self):
        """Arrival to seating, measured (None until both happened)."""
        if self.arrived_at and self.seated_at:
            return round((self.seated_at - self.arrived_at).total_seconds() / 60, 1)
        return None


class ReservationHistory(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=10, choices=Reservation.Status.choices, blank=True)
    to_status = models.CharField(max_length=10, choices=Reservation.Status.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "reservation history"

    def __str__(self):
        return f"{self.reservation.reference}: {self.from_status or '-'} -> {self.to_status}"
