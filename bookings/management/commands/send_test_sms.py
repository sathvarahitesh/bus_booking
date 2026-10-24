from django.core.management.base import BaseCommand, CommandError

from bookings.sms import provider_name, send_sms

STYLES = {'SENT': 'SUCCESS', 'SIMULATED': 'WARNING', 'UNAVAILABLE': 'WARNING', 'FAILED': 'ERROR'}


class Command(BaseCommand):
    help = 'Send a test SMS to check your SMS provider settings.'

    def add_arguments(self, parser):
        parser.add_argument('phone', help='10-digit mobile number, for example 7265976770')

    def handle(self, *args, **options):
        digits = ''.join(ch for ch in options['phone'] if ch.isdigit())[-10:]
        if len(digits) != 10:
            raise CommandError('Enter a 10-digit mobile number.')
        self.stdout.write(f"SMS provider: {provider_name() or '(none)'}")
        status, detail = send_sms(digits, 'YatraBus test SMS. Your SMS settings work.')
        style = getattr(self.style, STYLES.get(status, 'WARNING'))
        self.stdout.write(style(f'{status}: {detail}'))