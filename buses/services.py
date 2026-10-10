from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from .models import BusSchedule


def create_schedules(bus, route, start, end, departure_time, weekdays):
    """Add one trip per matching day between start and end (inclusive).

    weekdays: set of ints, Monday=0 ... Sunday=6.
    Returns counts: created, skipped (same trip exists), conflicts (bus busy on another trip).
    """
    existing = BusSchedule.objects.filter(
        bus=bus, travel_date__range=(start - timedelta(days=2), end + timedelta(days=1))
    ).select_related('route')
    busy = [(s.departure_datetime, s.arrival_datetime, s.route_id) for s in existing]

    new_trips = []
    skipped = conflicts = 0
    day = start
    while day <= end:
        if day.weekday() in weekdays:
            begin = datetime.combine(day, departure_time)
            finish = begin + route.duration
            clashes = [b for b in busy if b[0] < finish and begin < b[1]]
            if not clashes:
                new_trips.append(BusSchedule(
                    bus=bus, route=route, travel_date=day,
                    departure_time=departure_time, arrival_time=finish.time()))
                busy.append((begin, finish, route.id))
            elif any(b[0] == begin and b[2] == route.id for b in clashes):
                skipped += 1
            else:
                conflicts += 1
        day += timedelta(days=1)

    BusSchedule.objects.bulk_create(new_trips)
    return {'created': len(new_trips), 'skipped': skipped, 'conflicts': conflicts}


def extend_schedules(days_ahead):
    """Repeat every regular trip (bus + route + time) up to days_ahead days from today.

    A pattern's weekdays are taken from its trips in the last 30 days and the future.
    """
    today = timezone.localdate()
    end = today + timedelta(days=days_ahead)
    recent = BusSchedule.objects.filter(
        is_active=True, bus__is_active=True, travel_date__gte=today - timedelta(days=30),
    ).select_related('bus', 'route')

    patterns = {}
    for trip in recent:
        key = (trip.bus_id, trip.route_id, trip.departure_time)
        entry = patterns.setdefault(
            key, {'bus': trip.bus, 'route': trip.route, 'weekdays': set()})
        entry['weekdays'].add(trip.travel_date.weekday())

    totals = {'patterns': len(patterns), 'created': 0, 'skipped': 0, 'conflicts': 0}
    with transaction.atomic():
        for (_bus_id, _route_id, departure), entry in patterns.items():
            result = create_schedules(
                entry['bus'], entry['route'], today, end, departure, entry['weekdays'])
            for name in ('created', 'skipped', 'conflicts'):
                totals[name] += result[name]
    return totals