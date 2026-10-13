from django.core.management.base import BaseCommand, CommandError

from bookings.models import Booking
from bookings.services import access_url, issue_access_token


class Command(BaseCommand):
    help = 'Create a new secure access link for a booking. Older links stop working.'

    def add_arguments(self, parser):
        parser.add_argument('pnr', help='Booking PNR, for example PNR12345678')

    def handle(self, *args, **options):
        booking = Booking.objects.filter(pnr=options['pnr'].strip().upper()).first()
        if booking is None:
            raise CommandError('No booking with that PNR.')
        token = issue_access_token(booking)
        self.stdout.write(access_url(token))
        self.stdout.write(f'Valid until {booking.access_expires_at:%d %b %Y %H:%M}.')