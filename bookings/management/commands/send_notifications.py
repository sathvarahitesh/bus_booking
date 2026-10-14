from django.core.management.base import BaseCommand

from bookings.models import Notification
from bookings.notifications import MAX_TRIES, send_confirmation


class Command(BaseCommand):
    help = 'Retry confirmation emails and SMS that failed (up to 3 tries each).'

    def handle(self, *args, **options):
        failed = (Notification.objects
                  .filter(status='FAILED', attempts__lt=MAX_TRIES, booking__booking_status='CONFIRMED')
                  .select_related('booking').order_by('booking_id'))
        seen = set()
        for note in failed:
            if note.booking_id in seen:
                continue
            seen.add(note.booking_id)
            send_confirmation(note.booking, mode='retry')
        self.stdout.write(self.style.SUCCESS(f'{len(seen)} booking(s) retried.'))