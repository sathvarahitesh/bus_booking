from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Booking, Passenger

HOLD_MINUTES = 10


def release_bookings(booking_ids):
    """Cancel unpaid bookings and free their seats."""
    booking_ids = list(booking_ids)
    if not booking_ids:
        return
    with transaction.atomic():
        Passenger.objects.filter(booking_id__in=booking_ids).update(is_cancelled=True)
        Booking.objects.filter(pk__in=booking_ids).update(
            booking_status='CANCELLED', payment_status='FAILED')


def release_expired_holds():
    cutoff = timezone.now() - timedelta(minutes=HOLD_MINUTES)
    expired = Booking.objects.filter(booking_status='PENDING', created_at__lt=cutoff)
    release_bookings(expired.values_list('pk', flat=True))


def create_booking(user, data, contact, passenger_forms):
    """Create a pending booking and its passengers. Returns None if a seat was taken meanwhile."""
    schedule = data['schedule']
    total = schedule.fare * len(data['seats'])
    try:
        with transaction.atomic():
            booking = Booking.objects.create(
                user=user,
                schedule=schedule,
                boarding_point=data['boarding'],
                dropping_point=data['dropping'],
                contact_phone=contact['contact_phone'],
                contact_email=contact['contact_email'],
                total_amount=total,
            )
            for seat, form in passenger_forms:
                Passenger.objects.create(
                    booking=booking,
                    seat=seat,
                    name=form.cleaned_data['name'],
                    age=form.cleaned_data['age'],
                    gender=form.cleaned_data['gender'],
                )
    except IntegrityError:
        return None
    return booking