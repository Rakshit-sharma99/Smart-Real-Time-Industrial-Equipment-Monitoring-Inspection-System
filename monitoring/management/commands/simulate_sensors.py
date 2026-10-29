"""
Sensor simulator: sends fake readings to the running web server.

    python manage.py simulate_sensors [--interval N] [--equipment EQ-001] [--once] [--url http://127.0.0.1:8000]

Why HTTP? The in-memory channel layer only works inside the web server
process. Posting readings to /api/v1/ingest/ (like a real IoT gateway would)
keeps every database write and every WebSocket broadcast in that one process.

Each tick re-reads the active equipment and their sim modes from the database,
so changes made on the Settings page apply within one interval. Ctrl+C stops.
"""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from equipment.models import Equipment
from monitoring.models import SimulatorSettings
from monitoring.simulation import SensorSimulator

MAX_BACKOFF_SECONDS = 30


class ServerUnavailable(Exception):
    pass


class TokenRejected(Exception):
    pass


class IngestClient:
    """Tiny HTTP client using only the standard library."""

    def __init__(self, base_url, token):
        self.url = f"{base_url}/api/v1/ingest/"
        self.token = token
        # Talk to the server directly, never through a system proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send(self, readings):
        body = json.dumps({"readings": readings}).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Token {self.token}",
            },
        )
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise TokenRejected() from exc
            detail = exc.read().decode("utf-8", "replace")[:300]
            return {"accepted": 0, "rejected": [], "error": f"HTTP {exc.code}: {detail}"}
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ServerUnavailable(str(reason)) from exc


class Command(BaseCommand):
    help = "Send simulated sensor readings to the running web server. Press Ctrl+C to stop."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval", type=int, help="Seconds between batches (2-60). Default: value on the Settings page."
        )
        parser.add_argument("--equipment", metavar="CODE", help="Only simulate this machine, e.g. EQ-001.")
        parser.add_argument("--once", action="store_true", help="Send a single batch and exit.")
        parser.add_argument(
            "--url", default="http://127.0.0.1:8000", help="Web server base URL (default http://127.0.0.1:8000)."
        )

    def handle(self, *args, **options):
        interval, code, base_url = self.validate(options)
        client = IngestClient(base_url, settings.SIMULATOR_TOKEN)
        simulator = SensorSimulator()
        self.batches = self.readings_sent = self.failures = 0
        started = time.monotonic()

        self.stdout.write(self.style.SUCCESS(f"Simulator sending to {base_url}/api/v1/ingest/"))
        self.stdout.write("All values are SIMULATED. Press Ctrl+C to stop.\n")
        backoff = 0
        try:
            while True:
                sim_settings = SimulatorSettings.load()
                wait = interval or sim_settings.interval_seconds
                batch = self.build_batch(simulator, code, float(sim_settings.noise_pct))
                if not batch:
                    self.stdout.write("Nothing to send (no active machines, or all in OFFLINE mode).")
                else:
                    try:
                        self.send_batch(client, batch)
                        backoff = 0
                    except ServerUnavailable as exc:
                        self.failures += 1
                        if options["once"]:
                            raise CommandError(
                                f"Server not reachable at {base_url} ({exc}). Start it with: python manage.py runserver"
                            )
                        backoff = min(MAX_BACKOFF_SECONDS, backoff * 2 if backoff else 2)
                        self.stderr.write(
                            f"Server not reachable at {base_url} ({exc}). "
                            f"Is 'python manage.py runserver' running? Retrying in {backoff} s..."
                        )
                        time.sleep(backoff)
                        continue
                    except TokenRejected:
                        raise CommandError(
                            "The server rejected SIMULATOR_TOKEN. The server and simulator must use the same .env file."
                        )
                if options["once"]:
                    break
                time.sleep(wait)
        except KeyboardInterrupt:
            self.stdout.write("\nStopping simulator (Ctrl+C).")
        finally:
            elapsed = int(time.monotonic() - started)
            self.stdout.write(
                f"Summary: {self.batches} batches, {self.readings_sent} readings accepted, "
                f"{self.failures} failed attempts, ran {elapsed} s."
            )

    def validate(self, options):
        interval = options["interval"]
        if interval is not None and not 2 <= interval <= 60:
            raise CommandError("--interval must be between 2 and 60 seconds.")

        base_url = options["url"].rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise CommandError("--url must look like http://127.0.0.1:8000")

        code = options["equipment"]
        if code:
            code = code.strip().upper()
            equipment = Equipment.objects.filter(equipment_code=code).first()
            if equipment is None:
                raise CommandError(f"Unknown equipment code {code}.")
            if not equipment.is_active:
                raise CommandError(f"{code} is deactivated. Reactivate it first.")

        if not settings.SIMULATOR_TOKEN:
            raise CommandError("SIMULATOR_TOKEN is empty. Set it in .env (the server reads the same file).")
        return interval, code, base_url

    def build_batch(self, simulator, code, noise_pct):
        machines = Equipment.objects.active()
        if code:
            machines = machines.filter(equipment_code=code)
        batch = []
        for equipment in machines:
            values = simulator.next_values(equipment, noise_pct)
            if values is not None:
                batch.append({"equipment_code": equipment.equipment_code, **{k: str(v) for k, v in values.items()}})
        return batch

    def send_batch(self, client, batch):
        result = client.send(batch)
        accepted = result.get("accepted", 0)
        self.batches += 1
        self.readings_sent += accepted
        stamp = timezone.localtime().strftime("%H:%M:%S")
        line = f"[{stamp}] sent {len(batch)} readings, {accepted} accepted"
        rejected = result.get("rejected") or result.get("details", {}).get("rejected") or []
        if rejected:
            line += " | rejected: " + ", ".join(f"{r['equipment_code']} ({r['error']})" for r in rejected)
        if result.get("error"):
            line += f" | {result['error']}"
        self.stdout.write(line)
        self.stdout.flush()  # show each line immediately, even when output is piped
