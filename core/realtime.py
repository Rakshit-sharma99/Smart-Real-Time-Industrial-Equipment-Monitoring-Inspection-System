"""
Real-time event bus (Django Channels).

Services call publish() inside their database transaction; the event is sent
only after the transaction commits, so a browser never hears about a change
that was rolled back.

Events are small "something changed" messages:
    {"type": "table.changed", "id": "<unique id>", "topics": ["tables"], ...}
Browsers react by re-fetching the page fragments subscribed to those topics.
The fragments are ordinary permission-checked views, so the WebSocket never
carries data a user may not see (no customer names or phone numbers).

Groups:
    restaurant-<id>          every staff member of the restaurant (table changes)
    restaurant-<id>-<role>   one role, e.g. managers (tasks) or receptionists (bookings)
    user-<id>                one person (their tasks and notifications)
"""
import logging
import uuid
from functools import partial

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def restaurant_group(restaurant_id):
    return f"restaurant-{restaurant_id}"


def role_group(restaurant_id, role):
    return f"restaurant-{restaurant_id}-{str(role).lower()}"


def user_group(user_id):
    return f"user-{user_id}"


def publish(event_type, *, restaurant_id, topics, everyone=False, roles=(), user_ids=(), data=None):
    """Queue an event for the given audience; it is sent after the current transaction commits."""
    groups = set()
    if everyone:
        groups.add(restaurant_group(restaurant_id))
    groups.update(role_group(restaurant_id, role) for role in roles)
    groups.update(user_group(user_id) for user_id in user_ids if user_id)
    payload = {
        "type": event_type,
        "id": uuid.uuid4().hex,  # lets the browser drop duplicates (a user can be in several groups)
        "topics": list(topics),
        "at": timezone.now().isoformat(),
        **(data or {}),
    }
    transaction.on_commit(partial(send_now, sorted(groups), payload))


def send_now(groups, payload):
    """Send to every group. Never raises: a failed broadcast must not break the request."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    for group in groups:
        try:
            async_to_sync(channel_layer.group_send)(group, {"type": "live.event", "payload": payload})
        except Exception:  # noqa: BLE001 - log and carry on
            logger.exception("Could not send %s to %s", payload.get("type"), group)
