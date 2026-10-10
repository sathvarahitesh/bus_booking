import math
from urllib.parse import urlencode
from bookings.services import release_expired_holds

from django.http import JsonResponse
from django.views.decorators.http import require_GET

from django.contrib import messages
from django.db.models import Count, ExpressionWrapper, F, IntegerField, Max, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from .models import BoardingPoint, Bus, BusSchedule, DroppingPoint, Route, Seat
from django.utils import timezone

from .forms import SearchForm
from .models import Bus, BusSchedule, Route

# (key, label, start hour, end hour)
TIME_BANDS = [
    ('night', 'Night (12 AM - 6 AM)', 0, 6),
    ('morning', 'Morning (6 AM - 12 PM)', 6, 12),
    ('afternoon', 'Afternoon (12 PM - 6 PM)', 12, 18),
    ('evening', 'Evening (6 PM - 12 AM)', 18, 24),
]

SORTS = {
    'departure': 'departure_time',
    'price_low': 'bus__seat_price',
    'price_high': '-bus__seat_price',
    'seats': '-available_seats',
}
SORT_CHOICES = [
    ('departure', 'Earliest departure'),
    ('price_low', 'Price: low to high'),
    ('price_high', 'Price: high to low'),
    ('seats', 'Most seats available'),
]


def band_filter(field, selected):
    q = Q()
    for key, _label, start, end in TIME_BANDS:
        if key in selected:
            q |= Q(**{f'{field}__hour__gte': start, f'{field}__hour__lt': end})
    return q


def date_url(request, day):
    params = request.GET.copy()
    params['date'] = day.isoformat()
    return f'{request.path}?{params.urlencode()}'


def search(request):
    form = SearchForm(request.GET)
    if not form.is_valid():
        if request.GET:
            for field, errors in form.errors.items():
                for error in errors:
                    text = error if field == '__all__' else f'{field.title()}: {error}'
                    messages.error(request, text)
        else:
            messages.info(request, 'Please choose source, destination and travel date.')
        return redirect('core:home')
    release_expired_holds()

    source = form.cleaned_data['source']
    destination = form.cleaned_data['destination']
    travel_date = form.cleaned_data['date']
    today = timezone.localdate()

    # Trips for this route and date (before filters)
    trips = BusSchedule.objects.filter(
        is_active=True, bus__is_active=True, bus__operator__is_active=True,
        route__source__iexact=source, route__destination__iexact=destination,
        travel_date=travel_date,
    )
    if travel_date == today:
        trips = trips.filter(departure_time__gt=timezone.localtime().time())

    stats = trips.aggregate(low=Min('bus__seat_price'), high=Max('bus__seat_price'))
    has_trips = stats['high'] is not None
    price_low = math.floor(stats['low']) if has_trips else 0
    price_high = math.ceil(stats['high']) if has_trips else 0

    results = (
        trips.select_related('bus__operator', 'route')
        .prefetch_related('route__boarding_points', 'route__dropping_points')
        .annotate(booked=Count('passengers', filter=Q(passengers__is_cancelled=False)))
        .annotate(available_seats=ExpressionWrapper(
            F('bus__total_seats') - F('booked'), output_field=IntegerField()))
    )

    # Read and validate filters
    band_keys = [b[0] for b in TIME_BANDS]
    bus_types = [v for v in request.GET.getlist('bus_type') if v in dict(Bus.BUS_TYPES)]
    ac = request.GET.get('ac', '')
    ac = ac if ac in ('ac', 'non_ac') else ''
    seat_type = request.GET.get('seat_type', '')
    seat_type = seat_type if seat_type in dict(Bus.SEAT_TYPES) else ''
    dep = [v for v in request.GET.getlist('dep') if v in band_keys]
    arr = [v for v in request.GET.getlist('arr') if v in band_keys]
    sort = request.GET.get('sort', '')
    sort = sort if sort in SORTS else 'departure'
    try:
        max_price = int(request.GET.get('max_price', ''))
    except ValueError:
        max_price = None
    price_filtered = max_price is not None and max_price < price_high

    if bus_types:
        results = results.filter(bus__bus_type__in=bus_types)
    if ac == 'ac':
        results = results.filter(bus__is_ac=True)
    elif ac == 'non_ac':
        results = results.filter(bus__is_ac=False)
    if seat_type:
        results = results.filter(bus__seat_type=seat_type)
    if dep:
        results = results.filter(band_filter('departure_time', dep))
    if arr:
        results = results.filter(band_filter('arrival_time', arr))
    if price_filtered:
        results = results.filter(bus__seat_price__lte=max_price)

    schedules = list(results.order_by(SORTS[sort], 'departure_time'))
    has_filters = bool(bus_types or ac or seat_type or dep or arr or price_filtered)

    # Suggest the next date that has buses when nothing is found
    next_available_url = ''
    if not has_trips:
        next_day = (BusSchedule.objects.filter(
            is_active=True, bus__is_active=True,
            route__source__iexact=source, route__destination__iexact=destination,
            travel_date__gt=travel_date)
            .order_by('travel_date').values_list('travel_date', flat=True).first())
        if next_day:
            next_available_url = date_url(request, next_day)

    base_params = urlencode({'source': source, 'destination': destination,
                             'date': travel_date.isoformat()})
    context = {
        'source': source,
        'destination': destination,
        'travel_date': travel_date,
        'schedules': schedules,
        'has_trips': has_trips,
        'has_filters': has_filters,
        'clear_url': f'{request.path}?{base_params}',
        'prev_url': date_url(request, travel_date - timezone.timedelta(days=1)) if travel_date > today else '',
        'next_url': date_url(request, travel_date + timezone.timedelta(days=1)),
        'next_available_url': next_available_url,
        'price_low': price_low,
        'price_high': price_high,
        'bus_types': Bus.BUS_TYPES,
        'seat_types': Bus.SEAT_TYPES,
        'time_bands': TIME_BANDS,
        'sort_choices': SORT_CHOICES,
        'cities': sorted(set(Route.objects.values_list('source', flat=True))
                         | set(Route.objects.values_list('destination', flat=True))),
        'selected': {
            'bus_type': bus_types, 'ac': ac, 'seat_type': seat_type,
            'dep': dep, 'arr': arr, 'sort': sort,
            'max_price': max_price if price_filtered else price_high,
        },
    }
    return render(request, 'buses/search_results.html', context)


def route_list(request):
    return render(request, 'buses/route_list.html', {
        'routes': Route.objects.all(),
        'today': timezone.localdate(),
    })


MAX_SEATS = 6


def get_trip(schedule_id):
    return get_object_or_404(
        BusSchedule.objects.select_related('bus__operator', 'route'),
        pk=schedule_id, is_active=True, bus__is_active=True)


def build_decks(schedule, booked):
    """Group seats into decks and rows. Each row is a list with None for empty slots."""
    bus = schedule.bus
    columns = 4 if bus.seat_type == 'seater' else 3
    grouped = {}

    for seat in bus.seats.all():
        if seat.column > columns:
            continue

        seat.is_booked = seat.pk in booked
        seat.booked_gender = booked.get(seat.pk, '')

        rows = grouped.setdefault(seat.deck, {})
        rows.setdefault(seat.row, [None] * columns)[seat.column - 1] = seat

    names = dict(Seat.DECKS)

    return [
        {
            'name': f'{names[code]} Deck',
            'rows': [
                grouped[code][r]
                for r in sorted(grouped[code])
            ]
        }
        for code in sorted(grouped)
    ]


def validate_selection(request, schedule, booked_ids):
    """Server-side check of the posted seats and points. Returns (draft, error)."""
    try:
        seat_ids = {int(x) for x in request.POST.get('seats', '').split(',') if x}
    except ValueError:
        return None, 'Invalid seat selection.'
    if not seat_ids:
        return None, 'Please select at least one seat.'
    if len(seat_ids) > MAX_SEATS:
        return None, f'You can book at most {MAX_SEATS} seats at a time.'

    valid = set(Seat.objects.filter(bus=schedule.bus, pk__in=seat_ids).values_list('pk', flat=True))
    if valid != seat_ids:
        return None, 'Invalid seat selection.'
    if seat_ids & booked_ids:
        return None, 'Sorry, one of your seats was just booked by someone else. Please choose again.'

    try:
        boarding_id = int(request.POST.get('boarding_point', ''))
        dropping_id = int(request.POST.get('dropping_point', ''))
    except ValueError:
        return None, 'Please choose boarding and dropping points.'
    if (not BoardingPoint.objects.filter(pk=boarding_id, route=schedule.route).exists()
            or not DroppingPoint.objects.filter(pk=dropping_id, route=schedule.route).exists()):
        return None, 'Invalid boarding or dropping point.'

    return {
        'schedule_id': schedule.pk,
        'seat_ids': sorted(seat_ids),
        'boarding_id': boarding_id,
        'dropping_id': dropping_id,
    }, None


def seat_selection(request, schedule_id):
    release_expired_holds()
    schedule = get_trip(schedule_id)
    if schedule.departure_datetime <= timezone.localtime().replace(tzinfo=None):
        messages.error(request, 'This bus has already departed.')
        return redirect('bookings:passengers')

    booked = schedule.booked_seat_genders()
    booked_ids = set(booked)

    if request.method == 'POST':
        draft, error = validate_selection(request, schedule, booked_ids)
        if error:
            messages.error(request, error)
            return redirect('buses:seats', schedule_id=schedule.pk)
        request.session['booking_draft'] = draft
        return redirect('/bookings/passengers/')  # built in Phase 6

    return render(request, 'buses/seat_selection.html', {
        'schedule': schedule,
        'bus': schedule.bus,
        'decks': build_decks(schedule, booked),
        'max_seats': MAX_SEATS,
        'available_count': schedule.bus.total_seats - len(booked_ids),
    })


@require_GET
def seat_status(request, schedule_id):
    release_expired_holds()
    schedule = get_trip(schedule_id)
    booked = schedule.booked_seat_genders()
    return JsonResponse({'booked': booked, 'available': schedule.bus.total_seats - len(booked)})