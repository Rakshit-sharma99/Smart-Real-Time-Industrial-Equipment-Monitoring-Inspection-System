from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .broadcast import DASHBOARD_GROUP


class DashboardConsumer(AsyncJsonWebsocketConsumer):
    """
    ws/dashboard/ - a read-only live feed for logged-in users.

    The server pushes two message types (built in broadcast.py):
      {"type": "reading", ...}  after every processed sensor reading
      {"type": "alert", ...}    when an alert is created, escalated, acknowledged or resolved
    Anonymous connections are closed before the handshake completes.
    """

    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(DASHBOARD_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(DASHBOARD_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Clients do not send anything; incoming messages are ignored."""

    async def dashboard_message(self, event):
        """Handler for group_send(..., {"type": "dashboard.message", "payload": {...}})."""
        await self.send_json(event["payload"])
