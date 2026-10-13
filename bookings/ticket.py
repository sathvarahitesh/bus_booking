from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .pdf import render_ticket_pdf

BRAND = 'YatraBus'
TAGLINE = 'Online Bus Reservation and Ticket Booking System'
DISCLAIMER = 'Demo academic project. Payment was simulated. This is not a real ticket.'


def mask_phone(phone):
    return f"{phone[:2]}{'*' * (len(phone) - 4)}{phone[-2:]}" if len(phone) > 4 else phone


def mask_email_address(email):
    name, _, domain = email.partition('@')
    return f'{name[:2]}***@{domain}' if domain else ''


def policy_lines():
    """Cancellation policy text built from the same rules the cancel page uses."""
    from .utils import REFUND_RULES
    lines, previous = [], None
    for hours, percent in REFUND_RULES:
        if previous is None:
            lines.append(f'{hours} hours or more before departure: {percent}% refund')
        else:
            lines.append(f'{hours} to {previous} hours before departure: {percent}% refund')
        previous = hours
    lines.append(f'Less than {previous} hours before departure: cancellation not allowed')
    lines.append('Refunds apply to online payments only (demo).')
    return lines


def verify_url(booking):
    """Public verification address stored in the QR code. It contains only a random ticket id."""
    return f"{settings.SITE_URL}{reverse('bookings:verify', args=[booking.ensure_ticket_uid()])}"


def ticket_data(booking):
    """Everything printed on the ticket, read from the saved booking records."""
    schedule = booking.schedule
    bus, route = schedule.bus, schedule.route
    boarding, dropping = booking.boarding_point, booking.dropping_point
    passengers = list(
        booking.passengers.filter(is_cancelled=False).select_related('seat')
        .order_by('seat__deck', 'seat__row', 'seat__column'))
    payment = booking.payments.exclude(status='FAILED').order_by('-created_at').first()

    fare = schedule.fare
    boarding_at = schedule.departure_datetime - timedelta(minutes=boarding.minutes_before)
    dropping_at = schedule.arrival_datetime - timedelta(minutes=dropping.minutes_before)
    arrival_at = schedule.arrival_datetime

    if payment is not None:
        method = payment.get_method_display()
        transaction_id = payment.transaction_id
    else:
        method, transaction_id = 'Not recorded', ''
    pay_pending = booking.payment_status == 'PENDING'

    booked_on = timezone.localtime(booking.created_at)

    return {
        'brand': BRAND,
        'tagline': TAGLINE,
        'disclaimer': DISCLAIMER,
        'pnr': booking.pnr,
        'status': booking.get_booking_status_display(),
        'status_ok': booking.booking_status == 'CONFIRMED',
        'payment_status': booking.get_payment_status_display(),
        'payment_method': method,
        'transaction_id': transaction_id,
        'payment_note': ('Pay the fare at the counter before boarding.' if pay_pending
                         else 'Simulated (demo) payment'),
        'booked_on': booked_on.strftime('%d %b %Y, %I:%M %p'),
        'operator': bus.operator.name,
        'bus_number': bus.bus_number,
        'bus_type': (f"{bus.get_bus_type_display()} {'A/C' if bus.is_ac else 'Non A/C'} "
                     f"{bus.get_seat_type_display()}"),
        'source': route.source,
        'destination': route.destination,
        'distance': f'{route.distance_km} km',
        'duration': route.duration_display,
        'travel_date': schedule.travel_date.strftime('%a, %d %b %Y'),
        'departure_time': schedule.departure_time.strftime('%I:%M %p'),
        'arrival_date': arrival_at.strftime('%a, %d %b %Y'),
        'arrival_time': schedule.arrival_time.strftime('%I:%M %p'),
        'boarding_name': boarding.name,
        'boarding_address': boarding.address,
        'boarding_time': boarding_at.strftime('%I:%M %p'),
        'dropping_name': dropping.name,
        'dropping_address': dropping.address,
        'dropping_time': dropping_at.strftime('%I:%M %p, %d %b'),
        'contact_phone': mask_phone(booking.contact_phone),
        'contact_email': mask_email_address(booking.contact_email),
        'passengers': [
            {'name': p.name, 'age': p.age, 'gender': p.get_gender_display(), 'seat': p.seat.seat_number}
            for p in passengers],
        'fare_lines': [
            {'text': f'Seat {p.seat.seat_number} - {p.name}', 'amount': f'{fare:,.2f}'}
            for p in passengers],
        'total': f'{booking.total_amount:,.2f}',
        'verify_url': verify_url(booking),
        'policy': policy_lines(),
    }


def build_ticket_pdf(booking):
    base = Path(settings.BASE_DIR)
    return render_ticket_pdf(
        ticket_data(booking),
        font_dir=base / 'static' / 'fonts',
        logo_path=base / 'static' / 'img' / 'logo.png')  # optional: used only if the file exists


def ticket_filename(booking):
    return f'{BRAND}-{booking.pnr}.pdf'