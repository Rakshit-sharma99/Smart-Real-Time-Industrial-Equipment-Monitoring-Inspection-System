from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .permissions import get_restaurant, get_role
from .realtime import restaurant_group, role_group, user_group


class LiveConsumer(AsyncJsonWebsocketConsumer):
    """
    ws/live/ - read-only live feed for logged-in staff.

    Joins three groups: the restaurant, the user's role in that restaurant and
    the user personally. Anonymous users and accounts without a restaurant are
    refused before the handshake completes.
    """

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return
        role, restaurant_id = await self.staff_details(user)
        if role is None or restaurant_id is None:
            await self.close(code=4403)
            return
        self.groups_joined = [restaurant_group(restaurant_id), role_group(restaurant_id, role), user_group(user.pk)]
        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        for group in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Browsers do not send anything; incoming messages are ignored."""

    async def live_event(self, event):
        """Handler for group_send(..., {"type": "live.event", "payload": {...}})."""
        await self.send_json(event["payload"])

    @database_sync_to_async
    def staff_details(self, user):
        restaurant = get_restaurant(user)
        return get_role(user), restaurant.pk if restaurant else None
