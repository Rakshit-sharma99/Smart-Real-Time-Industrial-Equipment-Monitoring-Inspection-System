"""Notify managers and waiters about overdue tasks (for cron / Task Scheduler; dashboards also run this once a minute)."""
from django.core.management.base import BaseCommand

from tables.models import Restaurant
from tasks import services


class Command(BaseCommand):
    help = "Send overdue-task notifications for every restaurant."

    def handle(self, *args, **options):
        total = sum(services.notify_overdue_tasks(restaurant) for restaurant in Restaurant.objects.all())
        self.stdout.write(f"{total} overdue task(s) notified.")
