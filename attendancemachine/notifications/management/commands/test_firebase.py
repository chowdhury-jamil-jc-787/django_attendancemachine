from django.core.management.base import BaseCommand
from firebase_admin import messaging

from notifications.firebase import get_firebase_app


class Command(BaseCommand):
    help = "Test Firebase Admin SDK using dry-run message."

    def handle(self, *args, **options):
        get_firebase_app()

        message = messaging.Message(
            notification=messaging.Notification(
                title="Firebase Test",
                body="Firebase service account is working.",
            ),
            topic="local_test",
        )

        response = messaging.send(message, dry_run=True)

        self.stdout.write(self.style.SUCCESS("Firebase dry-run successful."))
        self.stdout.write(str(response))