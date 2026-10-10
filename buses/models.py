from datetime import datetime, timedelta

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class BusOperator(models.Model):
    name = models.CharField(max_length=100, unique=True)
    contact_phone = models.CharField(max_length=15, blank=True)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Bus(models.Model):
    BUS_TYPES = [
        ('volvo', 'Volvo'),
        ('scania', 'Scania'),
        ('mercedes', 'Mercedes'),
        ('standard', 'Standard'),
    ]
    SEAT_TYPES = [('seater', 'Seater'), ('sleeper', 'Sleeper')]

    operator = models.ForeignKey(BusOperator, on_delete=models.CASCADE, related_name='buses')
    bus_number = models.CharField(max_length=20, unique=True)
    bus_type = models.CharField(max_length=10, choices=BUS_TYPES, default='standard')
    is_ac = models.BooleanField('AC', default=True)
    seat_type = models.CharField(max_length=10, choices=SEAT_TYPES, default='seater')
    total_seats = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(60)])
    seat_price = models.DecimalField(max_digits=8, decimal_places=2)
    amenities = models.CharField(
        max_length=255, blank=True, help_text='Comma separated, e.g. WiFi, Charging Point')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['bus_number']
        verbose_name_plural = 'buses'

    def __str__(self):
        return f'{self.bus_number} ({self.operator.name})'

    @property
    def amenity_list(self):
        return [a.strip() for a in self.amenities.split(',') if a.strip()]

    def generate_seats(self):
        """Create the seat layout once. Seater: 2+2 per row. Sleeper: 2+1 per row, lower and upper deck."""
        if self.seats.exists():
            return 0
        seats = []
        if self.seat_type == 'seater':
            for i in range(self.total_seats):
                seats.append(Seat(bus=self, seat_number=str(i + 1),
                                  row=i // 4 + 1, column=i % 4 + 1, deck='L'))
        else:
            lower = (self.total_seats + 1) // 2
            upper = self.total_seats - lower
            for deck, count in (('L', lower), ('U', upper)):
                for i in range(count):
                    seats.append(Seat(bus=self, seat_number=f'{deck}{i + 1}',
                                      row=i // 3 + 1, column=i % 3 + 1, deck=deck))
        Seat.objects.bulk_create(seats)
        return len(seats)


class Seat(models.Model):
    DECKS = [('L', 'Lower'), ('U', 'Upper')]

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name='seats')
    seat_number = models.CharField(max_length=10)
    row = models.PositiveSmallIntegerField()
    column = models.PositiveSmallIntegerField()
    deck = models.CharField(max_length=1, choices=DECKS, default='L')

    class Meta:
        ordering = ['deck', 'row', 'column']
        constraints = [
            models.UniqueConstraint(fields=['bus', 'seat_number'], name='unique_seat_per_bus'),
        ]

    def __str__(self):
        return f'{self.seat_number} ({self.bus.bus_number})'


class Route(models.Model):
    source = models.CharField(max_length=60)
    destination = models.CharField(max_length=60)
    distance_km = models.PositiveIntegerField()
    duration = models.DurationField(help_text='Format HH:MM:SS, e.g. 09:30:00')

    class Meta:
        ordering = ['source', 'destination']
        constraints = [
            models.UniqueConstraint(fields=['source', 'destination'], name='unique_route'),
        ]

    def __str__(self):
        return f'{self.source} to {self.destination}'

    @property
    def duration_display(self):
        minutes = int(self.duration.total_seconds() // 60)
        return f'{minutes // 60}h {minutes % 60:02d}m'


class BoardingPoint(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='boarding_points')
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=200, blank=True)
    minutes_before = models.PositiveSmallIntegerField(
        default=30, help_text='Minutes before departure time when passengers should report')

    class Meta:
        ordering = ['-minutes_before']

    def __str__(self):
        return f'{self.name} ({self.route})'


class DroppingPoint(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='dropping_points')
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=200, blank=True)
    minutes_before = models.PositiveSmallIntegerField(
        default=0, help_text='Minutes before final arrival time when the bus reaches this point')

    class Meta:
        ordering = ['-minutes_before']

    def __str__(self):
        return f'{self.name} ({self.route})'


class BusSchedule(models.Model):
    """One bus trip on one route on one date."""
    bus = models.ForeignKey(Bus, on_delete=models.PROTECT, related_name='schedules')
    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name='schedules')
    travel_date = models.DateField()
    departure_time = models.TimeField()
    arrival_time = models.TimeField(editable=False)  # calculated from route duration
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['travel_date', 'departure_time']
        constraints = [
            models.UniqueConstraint(fields=['bus', 'travel_date', 'departure_time'],
                                    name='unique_bus_trip'),
        ]

    def __str__(self):
        return f'{self.route} | {self.travel_date} {self.departure_time:%H:%M} | {self.bus.bus_number}'

    def save(self, *args, **kwargs):
        departure = datetime.combine(self.travel_date, self.departure_time)
        self.arrival_time = (departure + self.route.duration).time()
        super().save(*args, **kwargs)

    @property
    def departure_datetime(self):
        return datetime.combine(self.travel_date, self.departure_time)

    @property
    def arrival_datetime(self):
        return self.departure_datetime + self.route.duration

    @property
    def fare(self):
        return self.bus.seat_price


    def boarding_times(self):
        return [(p, self.departure_datetime - timedelta(minutes=p.minutes_before))
                for p in self.route.boarding_points.all()]

    def dropping_times(self):
        return [(p, self.arrival_datetime - timedelta(minutes=p.minutes_before))
                for p in self.route.dropping_points.all()]

    def booked_seat_ids(self):
        return set(self.passengers.filter(is_cancelled=False).values_list('seat_id', flat=True))

    def booked_seat_genders(self):
        """{seat_id: 'M'/'F'/'O'} for seats already booked on this trip."""
        return dict(self.passengers.filter(is_cancelled=False).values_list('seat_id', 'gender'))