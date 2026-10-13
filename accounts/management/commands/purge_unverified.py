from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User


class Command(BaseCommand):
    help = 'Delete registrations whose email was never verified.'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24, help='Age in hours (default 24)')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])
        stale = User.objects.filter(email_verified=False, is_staff=False, date_joined__lt=cutoff)
        count = stale.count()
        stale.delete()
        self.stdout.write(self.style.SUCCESS(f'{count} unverified registration(s) deleted.'))