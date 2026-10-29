from django.conf import settings
from django.db import models


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    INSPECTOR = "INSPECTOR", "Inspector"


class UserProfile(models.Model):
    """Extra data for a Django user. Created automatically by a signal."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.INSPECTOR)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"
