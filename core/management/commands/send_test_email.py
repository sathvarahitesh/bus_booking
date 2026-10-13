from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Send a test email to check your email settings.'

    def add_arguments(self, parser):
        parser.add_argument('to', help='Recipient email address')

    def handle(self, *args, **options):
        backend = settings.MAILERS['default'].get(
            'BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
        self.stdout.write(f'Email backend: {backend}')
        try:
            sent = send_mail('YatraBus test email', 'Your email settings work.',
                             settings.DEFAULT_FROM_EMAIL, [options['to']])
        except Exception as exc:
            raise CommandError(f'Email failed: {exc}')
        if 'console' in backend:
            self.stdout.write(self.style.WARNING(
                'Console backend: the email was printed above, not delivered.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'{sent} email accepted by the mail server.'))