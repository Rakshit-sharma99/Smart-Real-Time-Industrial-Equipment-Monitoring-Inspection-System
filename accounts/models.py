from django.conf import settings
from django.db import models


class Role(models.TextChoices):
    MANAGER = "MANAGER", "Manager"
    WAITER = "WAITER", "Waiter"
    RECEPTIONIST = "RECEPTIONIST", "Receptionist"


class StaffProfile(models.Model):
    """Extra data for a Django user: role and restaurant. Created automatically by a signal."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    restaurant = models.ForeignKey(
        "tables.Restaurant", on_delete=models.PROTECT, null=True, blank=True, related_name="staff"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.WAITER)
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["user__username"]
        indexes = [models.Index(fields=["restaurant", "role"], name="staff_restaurant_role")]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
