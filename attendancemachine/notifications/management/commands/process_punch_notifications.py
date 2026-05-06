# notifications/management/commands/process_punch_notifications.py

from django.core.management.base import BaseCommand
from notifications.services import send_attendance_punch_notifications


class Command(BaseCommand):
    help = "Process attendance punches and send FCM notifications."

    def handle(self, *args, **options):
        send_attendance_punch_notifications()
        self.stdout.write(self.style.SUCCESS("Punch notifications processed."))