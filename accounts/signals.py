from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Role, UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile_for_new_user(sender, instance, created, **kwargs):
    """Every user gets a profile. Superusers start as Admin, everyone else as Inspector."""
    if created:
        default_role = Role.ADMIN if instance.is_superuser else Role.INSPECTOR
        UserProfile.objects.get_or_create(user=instance, defaults={"role": default_role})
