# from datetime import timedelta

# from django.db import IntegrityError, transaction
# from django.utils import timezone

# from .models import Booking, Passenger

# HOLD_MINUTES = 10


# def release_bookings(booking_ids):
#     """Cancel unpaid bookings and free their seats."""
#     booking_ids = list(booking_ids)
#     if not booking_ids:
#         return
#     with transaction.atomic():
#         Passenger.objects.filter(booking_id__in=booking_ids).update(is_cancelled=True)
#         Booking.objects.filter(pk__in=booking_ids).update(
#             booking_status='CANCELLED', payment_status='FAILED')


# def release_expired_holds():
#     cutoff = timezone.now() - timedelta(minutes=HOLD_MINUTES)
#     expired = Booking.objects.filter(booking_status='PENDING', created_at__lt=cutoff)
#     release_bookings(expired.values_list('pk', flat=True))


# def create_booking(user, data, contact, passenger_forms):
#     """Create a pending booking and its passengers. Returns None if a seat was taken meanwhile."""
#     schedule = data['schedule']
#     total = schedule.fare * len(data['seats'])
#     try:
#         with transaction.atomic():
#             booking = Booking.objects.create(
#                 user=user,
#                 schedule=schedule,
#                 boarding_point=data['boarding'],
#                 dropping_point=data['dropping'],
#                 contact_phone=contact['contact_phone'],
#                 contact_email=contact['contact_email'],
#                 total_amount=total,
#             )
#             for seat, form in passenger_forms:
#                 Passenger.objects.create(
#                     booking=booking,
#                     seat=seat,
#                     name=form.cleaned_data['name'],
#                     age=form.cleaned_data['age'],
#                     gender=form.cleaned_data['gender'],
#                 )
#     except IntegrityError:
#         return None
#     return booking
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from payments.models import Payment

from .models import Booking

ACCESS_DAYS_AFTER_TRAVEL = 30


def confirm_booking(booking, method):
    """Confirm a PENDING booking after a (demo) payment. Safe to call twice.

    The status change is a single conditional UPDATE, so only one caller can win.
    Returns (confirmed_now, payment). When the booking was already confirmed,
    cancelled or expired it returns (False, None) and creates no payment.
    """
    paid_online = method != 'COUNTER'
    with transaction.atomic():
        changed = Booking.objects.filter(pk=booking.pk, booking_status='PENDING').update(
            booking_status='CONFIRMED',
            payment_status='PAID' if paid_online else 'PENDING')
        if not changed:
            booking.refresh_from_db()
            return False, None
        payment = Payment.objects.create(
            booking=booking,
            method=method,
            amount=booking.total_amount,
            status='SUCCESS' if paid_online else 'PENDING',
            paid_at=timezone.now() if paid_online else None,
        )
        booking.refresh_from_db()
    return True, payment


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_access_token(booking):
    """Create a new secure access token. Any earlier token stops working.

    Only the SHA-256 hash is stored, so the raw token is returned once and cannot be read back.
    """
    token = secrets.token_urlsafe(32)
    departure = timezone.make_aware(booking.schedule.departure_datetime)
    booking.access_token_hash = _hash_token(token)
    booking.access_expires_at = departure + timedelta(days=ACCESS_DAYS_AFTER_TRAVEL)
    booking.save(update_fields=['access_token_hash', 'access_expires_at'])
    return token


def booking_for_token(token):
    """Return the booking for a valid, unexpired token, otherwise None."""
    if not token or len(token) > 100:
        return None
    booking = (Booking.objects
               .select_related('schedule')
               .filter(access_token_hash=_hash_token(token)).first())
    if booking is None or booking.access_expires_at is None:
        return None
    if booking.access_expires_at < timezone.now():
        return None
    return booking


def access_url(token):
    return f"{settings.SITE_URL}{reverse('bookings:access', args=[token])}"