from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import Bus, BusOperator, BusSchedule, Route
from .services import create_schedules


class ScheduleServiceTests(TestCase):
    def setUp(self):
        operator = BusOperator.objects.create(name='Test Travels')
        self.bus = Bus.objects.create(
            operator=operator, bus_number='TS-01-AA-0001', total_seats=8, seat_price=Decimal('500'))
        self.route = Route.objects.create(
            source='Alpha', destination='Beta', distance_km=100, duration=timedelta(hours=3))
        self.other_route = Route.objects.create(
            source='Beta', destination='Alpha', distance_km=100, duration=timedelta(hours=3))
        self.start = timezone.localdate() + timedelta(days=1)
        self.end = self.start + timedelta(days=6)  # 7 days

    def test_creates_one_trip_per_day_with_arrival_time(self):
        result = create_schedules(self.bus, self.route, self.start, self.end, time(21, 0), set(range(7)))
        self.assertEqual(result['created'], 7)
        self.assertEqual(BusSchedule.objects.count(), 7)
        self.assertEqual(BusSchedule.objects.first().arrival_time, time(0, 0))

    def test_running_twice_does_not_duplicate(self):
        create_schedules(self.bus, self.route, self.start, self.end, time(21, 0), set(range(7)))
        result = create_schedules(self.bus, self.route, self.start, self.end, time(21, 0), set(range(7)))
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['skipped'], 7)
        self.assertEqual(BusSchedule.objects.count(), 7)

    def test_busy_bus_is_not_double_scheduled(self):
        create_schedules(self.bus, self.route, self.start, self.end, time(21, 0), set(range(7)))
        result = create_schedules(self.bus, self.other_route, self.start, self.end, time(22, 0), set(range(7)))
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['conflicts'], 7)

    def test_weekday_selection(self):
        result = create_schedules(self.bus, self.route, self.start, self.end, time(9, 0), {0})
        self.assertEqual(result['created'], 1)  # 7 consecutive days contain one Monday