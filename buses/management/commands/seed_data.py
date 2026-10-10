from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from buses.models import (BoardingPoint, Bus, BusOperator, BusSchedule,
                          DroppingPoint, Route)

User = get_user_model()

CITY_POINTS = {
    'Ahmedabad': ['Paldi', 'Maninagar', 'Geeta Mandir'],
    'Surat': ['Adajan', 'Varachha', 'Kadodara'],
    'Vadodara': ['Alkapuri', 'Nizampura', 'Akota'],
    'Rajkot': ['Kalawad Road', 'Gondal Chowkdi', 'Bhaktinagar'],
    'Mumbai': ['Borivali', 'Andheri', 'Dadar'],
    'Pune': ['Swargate', 'Shivajinagar', 'Hinjewadi'],
    'Delhi': ['Kashmere Gate', 'Dhaula Kuan', 'Anand Vihar'],
    'Jaipur': ['Sindhi Camp', 'Narayan Singh Circle', 'Ajmeri Gate'],
    'Udaipur': ['Udaipole', 'Surajpole', 'Fatehpura'],
    'Bengaluru': ['Majestic', 'Silk Board', 'Marathahalli'],
    'Chennai': ['Koyambedu', 'Guindy', 'Tambaram'],
    'Hyderabad': ['Ameerpet', 'MGBS', 'LB Nagar'],
}

# (city A, city B, distance km, duration hours) - both directions are created
ROUTE_PAIRS = [
    ('Ahmedabad', 'Mumbai', 530, 9.5),
    ('Ahmedabad', 'Pune', 660, 11.5),
    ('Surat', 'Mumbai', 280, 5.0),
    ('Mumbai', 'Pune', 150, 3.5),
    ('Delhi', 'Jaipur', 280, 5.5),
    ('Jaipur', 'Udaipur', 400, 7.0),
    ('Bengaluru', 'Chennai', 350, 6.5),
    ('Hyderabad', 'Bengaluru', 570, 9.5),
    ('Vadodara', 'Ahmedabad', 110, 2.0),
    ('Vadodara', 'Surat', 150, 2.5),
    ('Ahmedabad', 'Rajkot', 215, 4.0),
]

OPERATORS = [
    ('Sunrise Travels', '9000000001'),
    ('Western Express', '9000000002'),
    ('BlueLine Tours', '9000000003'),
    ('Royal Voyager', '9000000004'),
]

# (operator, number, type, AC, seat type, seats, price, amenities)
BUSES = [
    ('Sunrise Travels', 'GJ-01-AB-1001', 'volvo', True, 'sleeper', 30, 1100, 'WiFi, Charging Point, Blanket, Water Bottle'),
    ('Sunrise Travels', 'GJ-01-AB-1002', 'volvo', True, 'seater', 40, 750, 'Charging Point, Water Bottle, Reading Light'),
    ('Western Express', 'GJ-05-CD-2001', 'scania', True, 'sleeper', 30, 1250, 'WiFi, Charging Point, Blanket, Snacks'),
    ('Western Express', 'GJ-05-CD-2002', 'standard', False, 'seater', 44, 450, 'Charging Point'),
    ('BlueLine Tours', 'MH-12-EF-3001', 'mercedes', True, 'seater', 40, 900, 'WiFi, Charging Point, Water Bottle'),
    ('BlueLine Tours', 'MH-12-EF-3002', 'standard', False, 'sleeper', 30, 700, 'Charging Point, Blanket'),
    ('Royal Voyager', 'RJ-14-GH-4001', 'volvo', True, 'sleeper', 30, 1000, 'Charging Point, Blanket, Water Bottle'),
    ('Royal Voyager', 'KA-01-IJ-5001', 'scania', True, 'seater', 40, 850, 'WiFi, Charging Point, Reading Light'),
    ('Royal Voyager', 'KA-01-IJ-5002', 'standard', False, 'seater', 40, 500, 'Charging Point'),
    ('Sunrise Travels', 'TS-09-KL-6001', 'mercedes', True, 'sleeper', 30, 1300, 'WiFi, Charging Point, Blanket, Snacks'),
]

MORNING_TIMES = [time(6, 30), time(9, 0), time(14, 0)]
NIGHT_TIMES = [time(19, 0), time(21, 30), time(22, 30)]


class Command(BaseCommand):
    help = 'Load sample data: users, operators, buses, seats, routes, points and schedules.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30, help='Number of days of schedules')

    def handle(self, *args, **options):
        self.create_users()
        operators = self.create_operators()
        buses = self.create_buses(operators)
        routes = self.create_routes()
        created = self.create_schedules(buses, routes, options['days'])

        self.stdout.write(self.style.SUCCESS(
            f'Done: {len(operators)} operators, {len(buses)} buses, {len(routes)} routes, '
            f'{created} new schedules.'))
        self.stdout.write('Create your admin with: python manage.py createsuperuser')
        self.stdout.write('Customer login: demo / demo')

    # def create_users(self):
    #     if not User.objects.filter(username='admin').exists():
    #         User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
    #     if not User.objects.filter(username='demo').exists():
    #         User.objects.create_user('demo', 'demo@example.com', 'demo',
    #                                  first_name='Demo', last_name='User', phone='9876543210')

    def create_users(self):
        if not User.objects.filter(username='demo').exists():
            User.objects.create_user('demo', 'demo@example.com', 'demo',
                                     first_name='Demo', last_name='User', phone='9876543210')

    def create_operators(self):
        operators = {}
        for name, phone in OPERATORS:
            operators[name], _ = BusOperator.objects.get_or_create(
                name=name, defaults={'contact_phone': phone})
        return operators

    def create_buses(self, operators):
        buses = []
        for op, number, bus_type, is_ac, seat_type, seats, price, amenities in BUSES:
            bus, _ = Bus.objects.get_or_create(bus_number=number, defaults={
                'operator': operators[op], 'bus_type': bus_type, 'is_ac': is_ac,
                'seat_type': seat_type, 'total_seats': seats, 'seat_price': price,
                'amenities': amenities,
            })
            bus.generate_seats()
            buses.append(bus)
        return buses

    def create_routes(self):
        routes = []
        for city_a, city_b, km, hours in ROUTE_PAIRS:
            for source, destination in ((city_a, city_b), (city_b, city_a)):
                route, _ = Route.objects.get_or_create(
                    source=source, destination=destination,
                    defaults={'distance_km': km, 'duration': timedelta(hours=hours)})
                for name, mins in zip(CITY_POINTS[source], (45, 30, 15)):
                    BoardingPoint.objects.get_or_create(
                        route=route, name=name,
                        defaults={'address': f'{name}, {source}', 'minutes_before': mins})
                for name, mins in zip(CITY_POINTS[destination], (40, 20, 0)):
                    DroppingPoint.objects.get_or_create(
                        route=route, name=name,
                        defaults={'address': f'{name}, {destination}', 'minutes_before': mins})
                routes.append(route)
        return routes

    def create_schedules(self, buses, routes, days):
        today = timezone.localdate()
        created = 0
        for i, route in enumerate(routes):
            trips = [
                (buses[i % len(buses)], MORNING_TIMES[i % 3]),
                (buses[(i + 3) % len(buses)], NIGHT_TIMES[i % 3]),
            ]
            for bus, departure in trips:
                for d in range(days):
                    _, was_created = BusSchedule.objects.get_or_create(
                        bus=bus, travel_date=today + timedelta(days=d),
                        departure_time=departure, defaults={'route': route})
                    created += was_created
        return created