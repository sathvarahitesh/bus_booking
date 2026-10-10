from django.core.management.base import BaseCommand

from buses.services import extend_schedules


class Command(BaseCommand):
    help = 'Repeat existing bus trips so schedules exist for the next N days.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=60, help='Days ahead (default 60)')

    def handle(self, *args, **options):
        result = extend_schedules(options['days'])
        self.stdout.write(self.style.SUCCESS(
            f"{result['patterns']} regular trips checked: {result['created']} created, "
            f"{result['skipped']} already existed, {result['conflicts']} skipped (bus busy)."))