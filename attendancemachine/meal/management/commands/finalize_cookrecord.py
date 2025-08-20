# meal/management/commands/finalize_cookrecord.py
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from meal.services import generate_cook_record

User = get_user_model()

class Command(BaseCommand):
    help = "Generate and finalize today's CookRecord (run at 08:00 Asia/Dhaka)."

    def add_arguments(self, parser):
        parser.add_argument('--username', help='Username to attribute finalization to', default=None)

    def handle(self, *args, **options):
        user = None
        if options['username']:
            try:
                user = User.objects.get(username=options['username'])
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User {options['username']} not found"))
                return

        today = timezone.localdate()
        rec = generate_cook_record(today, finalized_by=user, force=True)
        rec.is_finalized = True
        rec.finalized_at = timezone.now()
        rec.finalized_by = user
        rec.save()
        self.stdout.write(self.style.SUCCESS(f"CookRecord for {today} finalized (id={rec.id})"))
