"""
Send one fake camera detection to the running server, to try the integration API.

    python manage.py send_test_detection --table 12 --people 0 --dishes 1 [--confidence 0.9] [--url http://127.0.0.1:8000]

This stands in for a computer-vision service. RestaurantSpy itself has no
vision model: a real service would post the same JSON after analysing a frame.
"""
import json
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tables.models import RestaurantTable


class Command(BaseCommand):
    help = "POST a fake camera detection to /api/v1/detections/ (needs the server running)."

    def add_arguments(self, parser):
        parser.add_argument("--table", type=int, required=True, help="Table number, e.g. 12")
        parser.add_argument("--people", type=int, choices=[0, 1], required=True, help="1 = customers seated")
        parser.add_argument("--dishes", type=int, choices=[0, 1], required=True, help="1 = plates/glasses on the table")
        parser.add_argument("--confidence", type=float, default=0.9)
        parser.add_argument("--url", default="http://127.0.0.1:8000")

    def handle(self, *args, **options):
        if not settings.DETECTION_API_TOKEN:
            raise CommandError("DETECTION_API_TOKEN is empty in .env.")
        table = RestaurantTable.objects.filter(number=options["table"]).first()
        if table is None:
            raise CommandError(f"No table T{options['table']}.")
        body = json.dumps({
            "table_id": str(table.public_id), "customers_present": bool(options["people"]),
            "dishes_present": bool(options["dishes"]), "confidence": options["confidence"], "device_id": "test-camera",
        }).encode()
        request = urllib.request.Request(
            options["url"].rstrip("/") + "/api/v1/detections/", data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Token {settings.DETECTION_API_TOKEN}"},
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
                self.stdout.write(self.style.SUCCESS(response.read().decode()))
        except urllib.error.HTTPError as exc:
            raise CommandError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as exc:
            raise CommandError(f"Server not reachable at {options['url']} ({exc.reason}). Is runserver running?")
