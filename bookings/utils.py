from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Booking, Passenger

HOLD_MINUTES = 10


def get_customer(request):
    """The logged-in customer, or None for guests (admin accounts are ignored here)."""
    user = request.user
    if user.is_authenticated and not user.is_staff:
        return user
    return None


def grant_access(request, booking):
    """Let this browser session view a (guest) booking."""
    access = request.session.get('booking_access', [])
    if booking.pnr not in access:
        access.append(booking.pnr)
        request.session['booking_access'] = access


def can_view(request, booking):
    customer = get_customer(request)
    if customer and booking.user_id == customer.id:
        return True
    return booking.pnr in request.session.get('booking_access', [])


def release_expired_holds():
    """Free the seats of unpaid bookings older than HOLD_MINUTES."""
    cutoff = timezone.now() - timedelta(minutes=HOLD_MINUTES)
    ids = list(Booking.objects.filter(
        booking_status='PENDING', payment_status='PENDING', created_at__lt=cutoff,
    ).values_list('pk', flat=True))
    if ids:
        Passenger.objects.filter(booking_id__in=ids).update(is_cancelled=True)
        Booking.objects.filter(pk__in=ids).update(booking_status='CANCELLED', payment_status='FAILED')


# (hours before departure, refund percent), checked from the top
REFUND_RULES = [(24, 90), (12, 50), (2, 25)]


def refund_policy(booking):
    """Cancellation rules: whether it is allowed and how much is refunded."""
    now = timezone.localtime().replace(tzinfo=None)
    hours_left = (booking.schedule.departure_datetime - now).total_seconds() / 3600
    percent = 0
    for hours, pct in REFUND_RULES:
        if hours_left >= hours:
            percent = pct
            break
    refund = Decimal('0')
    if booking.payment_status == 'PAID':
        refund = (booking.total_amount * percent / 100).quantize(Decimal('0.01'))
    return {'allowed': percent > 0, 'percent': percent, 'refund': refund}