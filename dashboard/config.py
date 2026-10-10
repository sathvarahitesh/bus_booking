from django.utils import timezone

from accounts.models import User
from bookings.models import Booking, Cancellation
from buses.models import (BoardingPoint, Bus, BusOperator, BusSchedule,
                          DroppingPoint, Route, Seat)
from payments.models import Payment

ACTIVE = [('1', 'Active'), ('0', 'Inactive')]
YES_NO = [('1', 'Yes'), ('0', 'No')]


def active_filter():
    return {'param': 'active', 'lookup': 'is_active', 'label': 'Status', 'type': 'bool', 'choices': ACTIVE}


# ---- hooks run after a record is saved ----
def bus_created(bus):
    bus.generate_seats()


def booking_saved(booking):
    if booking.booking_status == 'CANCELLED':
        booking.passengers.update(is_cancelled=True)  # free the seats
        Cancellation.objects.get_or_create(
            booking=booking, defaults={'reason': 'Cancelled by admin', 'refund_amount': 0})


def payment_saved(payment):
    booking = payment.booking
    if payment.status == 'SUCCESS':
        if not payment.paid_at:
            payment.paid_at = timezone.now()
            payment.save(update_fields=['paid_at'])
        booking.payment_status = 'PAID'
        booking.save(update_fields=['payment_status'])
    elif payment.status == 'REFUNDED':
        booking.payment_status = 'REFUNDED'
        booking.save(update_fields=['payment_status'])


# ---- one entry per manageable section ----
ENTITIES = {
    'users': {
        'model': User, 'title': 'Customers', 'singular': 'Customer', 'icon': 'bi-people',
        'base_filter': {'is_staff': False},
        'columns': [('Username', 'username'), ('Name', 'get_full_name'), ('Email', 'email'),
                    ('Phone', 'phone'), ('Joined', 'date_joined'), ('Active', 'is_active')],
        'search': ['username', 'email', 'first_name', 'last_name', 'phone'],
        'filters': [active_filter()],
        'fields': ['first_name', 'last_name', 'email', 'phone', 'gender', 'is_active'],
        'create': False, 'edit': True, 'delete': False,
    },
    'operators': {
        'model': BusOperator, 'title': 'Bus Operators', 'singular': 'Operator', 'icon': 'bi-building',
        'columns': [('Name', 'name'), ('Phone', 'contact_phone'), ('Email', 'contact_email'),
                    ('Active', 'is_active')],
        'search': ['name', 'contact_phone'],
        'filters': [active_filter()],
        'fields': ['name', 'contact_phone', 'contact_email', 'is_active'],
        'create': True, 'edit': True, 'delete': True,
    },
    'buses': {
        'model': Bus, 'title': 'Buses', 'singular': 'Bus', 'icon': 'bi-bus-front',
        'select_related': ['operator'],
        'columns': [('Bus No.', 'bus_number'), ('Operator', 'operator.name'),
                    ('Type', 'get_bus_type_display'), ('AC', 'is_ac'),
                    ('Seat type', 'get_seat_type_display'), ('Seats', 'total_seats'),
                    ('Price', 'seat_price'), ('Active', 'is_active')],
        'search': ['bus_number', 'operator__name'],
        'filters': [
            active_filter(),
            {'param': 'type', 'lookup': 'bus_type', 'label': 'Bus type', 'type': 'choice',
             'choices': Bus.BUS_TYPES},
            {'param': 'ac', 'lookup': 'is_ac', 'label': 'AC', 'type': 'bool', 'choices': YES_NO},
            {'param': 'seat', 'lookup': 'seat_type', 'label': 'Seat type', 'type': 'choice',
             'choices': Bus.SEAT_TYPES},
        ],
        'fields': ['operator', 'bus_number', 'bus_type', 'is_ac', 'seat_type', 'total_seats',
                   'seat_price', 'amenities', 'is_active'],
        'create': True, 'edit': True, 'delete': True,
        'after_create': bus_created,
    },
    'routes': {
        'model': Route, 'title': 'Routes', 'singular': 'Route', 'icon': 'bi-signpost-split',
        'columns': [('Source', 'source'), ('Destination', 'destination'),
                    ('Distance (km)', 'distance_km'), ('Duration', 'duration_display')],
        'search': ['source', 'destination'],
        'fields': ['source', 'destination', 'distance_km', 'duration'],
        'create': True, 'edit': True, 'delete': True,
    },
    'boarding-points': {
        'model': BoardingPoint, 'title': 'Boarding Points', 'singular': 'Boarding Point',
        'icon': 'bi-geo-alt', 'select_related': ['route'],
        'columns': [('Route', 'route'), ('Name', 'name'), ('Address', 'address'),
                    ('Min. before departure', 'minutes_before')],
        'search': ['name', 'route__source', 'route__destination'],
        'fields': ['route', 'name', 'address', 'minutes_before'],
        'create': True, 'edit': True, 'delete': True,
    },
    'dropping-points': {
        'model': DroppingPoint, 'title': 'Dropping Points', 'singular': 'Dropping Point',
        'icon': 'bi-geo-alt-fill', 'select_related': ['route'],
        'columns': [('Route', 'route'), ('Name', 'name'), ('Address', 'address'),
                    ('Min. before arrival', 'minutes_before')],
        'search': ['name', 'route__source', 'route__destination'],
        'fields': ['route', 'name', 'address', 'minutes_before'],
        'create': True, 'edit': True, 'delete': True,
    },
    'schedules': {
        'model': BusSchedule, 'title': 'Schedules', 'singular': 'Schedule', 'icon': 'bi-calendar-event',
        'select_related': ['bus', 'route'],
        'columns': [('Route', 'route'), ('Bus', 'bus.bus_number'), ('Date', 'travel_date'),
                    ('Departs', 'departure_time'), ('Arrives', 'arrival_time'), ('Active', 'is_active')],
        'search': ['bus__bus_number', 'route__source', 'route__destination'],
        'filters': [
            active_filter(),
            {'param': 'date', 'lookup': 'travel_date', 'label': 'Travel date', 'type': 'date'},
        ],
        'fields': ['bus', 'route', 'travel_date', 'departure_time', 'is_active'],
        'create': True, 'edit': True, 'delete': True,
    },
    'seats': {
        'model': Seat, 'title': 'Seats', 'singular': 'Seat', 'icon': 'bi-grid-3x3-gap',
        'select_related': ['bus'],
        'columns': [('Bus', 'bus.bus_number'), ('Seat', 'seat_number'), ('Deck', 'get_deck_display'),
                    ('Row', 'row'), ('Column', 'column')],
        'search': ['bus__bus_number', 'seat_number'],
        'filters': [{'param': 'deck', 'lookup': 'deck', 'label': 'Deck', 'type': 'choice',
                     'choices': Seat.DECKS}],
        'fields': ['bus', 'seat_number', 'row', 'column', 'deck'],
        'create': True, 'edit': True, 'delete': True,
    },
    'bookings': {
        'model': Booking, 'title': 'Bookings', 'singular': 'Booking', 'icon': 'bi-ticket-perforated',
        'select_related': ['user', 'schedule__route'],
        'columns': [('PNR', 'pnr'), ('Account', 'user.username'), ('Route', 'schedule.route'),
                    ('Travel date', 'schedule.travel_date'), ('Amount', 'total_amount'),
                    ('Booking', 'get_booking_status_display'), ('Payment', 'get_payment_status_display'),
                    ('Booked on', 'created_at')],
        'search': ['pnr', 'contact_phone', 'user__username', 'passengers__name'],
        'filters': [
            {'param': 'status', 'lookup': 'booking_status', 'label': 'Booking', 'type': 'choice',
             'choices': Booking.BOOKING_STATUS},
            {'param': 'pay', 'lookup': 'payment_status', 'label': 'Payment', 'type': 'choice',
             'choices': Booking.PAYMENT_STATUS},
            {'param': 'date', 'lookup': 'schedule__travel_date', 'label': 'Travel date', 'type': 'date'},
        ],
        'fields': ['booking_status', 'payment_status', 'contact_phone', 'contact_email'],
        'create': False, 'edit': True, 'delete': False, 'detail': True,
        'after_save': booking_saved,
    },
    'cancellations': {
        'model': Cancellation, 'title': 'Cancellations', 'singular': 'Cancellation', 'icon': 'bi-x-circle',
        'select_related': ['booking'],
        'columns': [('PNR', 'booking.pnr'), ('Refund', 'refund_amount'), ('Reason', 'reason'),
                    ('Cancelled at', 'cancelled_at')],
        'search': ['booking__pnr', 'reason'],
        'fields': ['reason', 'refund_amount'],
        'create': False, 'edit': True, 'delete': False,
    },
    'payments': {
        'model': Payment, 'title': 'Payments', 'singular': 'Payment', 'icon': 'bi-credit-card',
        'select_related': ['booking'],
        'columns': [('Transaction', 'transaction_id'), ('PNR', 'booking.pnr'),
                    ('Method', 'get_method_display'), ('Amount', 'amount'),
                    ('Status', 'get_status_display'), ('Paid at', 'paid_at')],
        'search': ['transaction_id', 'booking__pnr'],
        'filters': [
            {'param': 'status', 'lookup': 'status', 'label': 'Status', 'type': 'choice',
             'choices': Payment.STATUSES},
            {'param': 'method', 'lookup': 'method', 'label': 'Method', 'type': 'choice',
             'choices': Payment.METHODS},
        ],
        'fields': ['status'],
        'create': False, 'edit': True, 'delete': False,
        'after_save': payment_saved,
    },
}

NAV = [(key, cfg['title'], cfg['icon']) for key, cfg in ENTITIES.items()]