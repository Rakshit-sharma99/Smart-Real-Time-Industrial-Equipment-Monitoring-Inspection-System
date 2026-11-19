from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Role, StaffProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    """Every user gets a staff profile. Superusers start as Manager, everyone else as Waiter."""
    if created:
        default_role = Role.MANAGER if instance.is_superuser else Role.WAITER
        StaffProfile.objects.get_or_create(user=instance, defaults={"role": default_role})
