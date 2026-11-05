"""In-app notifications. Saved in the database and pushed to the recipient in real time."""
from django.contrib.auth.models import User

from core import realtime

from .models import Notification


def staff_with_roles(restaurant, *roles):
    """Active staff of one restaurant with any of the given roles."""
    return User.objects.filter(is_active=True, profile__restaurant=restaurant, profile__role__in=roles)


def notify(recipients, *, restaurant, kind, title, message="", link=""):
    """Create one notification per recipient and push it to each of them after commit."""
    users = {user.pk: user for user in recipients if user is not None and user.is_active}
    if not users:
        return []
    rows = Notification.objects.bulk_create(
        Notification(recipient_id=pk, restaurant=restaurant, kind=kind, title=title, message=message, link=link)
        for pk in users
    )
    realtime.publish(
        "notification",
        restaurant_id=restaurant.pk,
        topics=["notifications"],
        user_ids=list(users),
        data={"kind": kind, "title": title, "message": message, "link": link},
    )
    return rows


def notify_roles(restaurant, roles, **kwargs):
    return notify(staff_with_roles(restaurant, *roles), restaurant=restaurant, **kwargs)


def unread_count(user):
    return Notification.objects.filter(recipient=user, is_read=False).count()


def mark_read(user, ids=None):
    """Mark the user's notifications as read (all of them, or only `ids`). Returns how many changed."""
    unread = Notification.objects.filter(recipient=user, is_read=False)
    if ids is not None:
        unread = unread.filter(pk__in=ids)
    return unread.update(is_read=True)
