from datetime import timedelta

from django.contrib.auth.views import redirect_to_login
from core.throttle import limit_reached, throttle_post

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from buses.models import BoardingPoint, BusSchedule, DroppingPoint, Seat

from .forms import ContactForm, FindBookingForm, PassengerForm
from .models import Booking, Cancellation

from django.views.decorators.cache import never_cache
from .services import booking_for_token

from django.http import Http404, HttpResponse

from .ticket import build_ticket_pdf, ticket_filename

from .utils import (HOLD_MINUTES, can_view, get_customer, grant_access,
                    refund_policy, release_expired_holds)


def passenger_details(request):
    release_expired_holds()
    draft = request.session.get('booking_draft')
    if not draft:
        messages.info(request, 'Please select your seats first.')
        return redirect('core:home')

    schedule = get_object_or_404(
        BusSchedule.objects.select_related('bus__operator', 'route'),
        pk=draft['schedule_id'], is_active=True)
    if schedule.departure_datetime <= timezone.localtime().replace(tzinfo=None):
        messages.error(request, 'This bus has already departed.')
        return redirect('core:home')

    seats = list(Seat.objects.filter(bus=schedule.bus, pk__in=draft['seat_ids']))
    boarding = get_object_or_404(BoardingPoint, pk=draft['boarding_id'], route=schedule.route)
    dropping = get_object_or_404(DroppingPoint, pk=draft['dropping_id'], route=schedule.route)

    booked = schedule.booked_seat_ids()
    if len(seats) != len(draft['seat_ids']) or any(s.pk in booked for s in seats):
        messages.error(request, 'Some of your seats are no longer available. Please choose again.')
        return redirect('buses:seats', schedule_id=schedule.pk)

    customer = get_customer(request)
    initial = {'phone': customer.phone, 'email': customer.email} if customer else {}
    data = request.POST or None
    seat_forms = [(seat, PassengerForm(data, prefix=f's{seat.pk}')) for seat in seats]
    contact_form = ContactForm(data, initial=initial)
    total = schedule.fare * len(seats)

    if request.method == 'POST':
        results = [form.is_valid() for _seat, form in seat_forms]
        if all(results) and contact_form.is_valid():
            try:
                with transaction.atomic():
                    booking = Booking.objects.create(
                        user=customer,
                        schedule=schedule,
                        boarding_point=boarding,
                        dropping_point=dropping,
                        contact_phone=contact_form.cleaned_data['phone'],
                        contact_email=contact_form.cleaned_data['email'],
                        total_amount=total,
                    )
                    for seat, form in seat_forms:
                        passenger = form.save(commit=False)
                        passenger.booking = booking
                        passenger.seat = seat
                        passenger.save()
            except IntegrityError:
                messages.error(request, 'Sorry, one of your seats was just booked by someone else.')
                return redirect('buses:seats', schedule_id=schedule.pk)

            request.session.pop('booking_draft', None)
            grant_access(request, booking)
            return redirect('bookings:detail', pnr=booking.pnr)

    return render(request, 'bookings/passenger_details.html', {
        'schedule': schedule,
        'seat_forms': seat_forms,
        'contact_form': contact_form,
        'boarding': boarding,
        'dropping': dropping,
        'boarding_time': schedule.departure_datetime - timedelta(minutes=boarding.minutes_before),
        'dropping_time': schedule.arrival_datetime - timedelta(minutes=dropping.minutes_before),
        'total': total,
        'hold_minutes': HOLD_MINUTES,
    })


def booking_detail(request, pnr):
    release_expired_holds()
    booking = get_object_or_404(
        Booking.objects.select_related(
            'schedule__bus__operator', 'schedule__route', 'boarding_point', 'dropping_point'),
        pnr=pnr)
    if not can_view(request, booking):
        messages.error(request, 'You do not have access to this booking.')
        return redirect('core:home')

    schedule = booking.schedule
    return render(request, 'bookings/booking_detail.html', {
        'booking': booking,
        'schedule': schedule,
        'passengers': booking.passengers.select_related('seat'),
        'boarding_time': schedule.departure_datetime - timedelta(minutes=booking.boarding_point.minutes_before),
        'dropping_time': schedule.arrival_datetime - timedelta(minutes=booking.dropping_point.minutes_before),
        'expires_at': booking.created_at + timedelta(minutes=HOLD_MINUTES),
        'policy': refund_policy(booking),
    })

def ticket(request, pnr):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'schedule__bus__operator', 'schedule__route', 'boarding_point', 'dropping_point'),
        pnr=pnr)
    if not can_view(request, booking):
        messages.error(request, 'You do not have access to this ticket.')
        return redirect('core:home')
    if booking.booking_status != 'CONFIRMED':
        messages.warning(request, 'The e-ticket is available after the booking is confirmed.')
        return redirect('bookings:detail', pnr=booking.pnr)

    schedule = booking.schedule
    return render(request, 'bookings/ticket.html', {
        'booking': booking,
        'schedule': schedule,
        'passengers': booking.passengers.select_related('seat'),
        'boarding_time': schedule.departure_datetime - timedelta(minutes=booking.boarding_point.minutes_before),
        'dropping_time': schedule.arrival_datetime - timedelta(minutes=booking.dropping_point.minutes_before),
    })


def my_bookings(request):
    customer = get_customer(request)
    if not customer:
        return redirect_to_login(request.get_full_path())

    release_expired_holds()
    today = timezone.localdate()
    bookings = (customer.bookings
                .select_related('schedule__route', 'schedule__bus__operator', 'cancellation')
                .prefetch_related('passengers__seat'))

    upcoming, past, cancelled = [], [], []
    for booking in bookings:
        if booking.booking_status == 'CANCELLED':
            if hasattr(booking, 'cancellation'):  # hide expired unpaid holds
                cancelled.append(booking)
        elif booking.schedule.travel_date >= today:
            upcoming.append(booking)
        else:
            past.append(booking)

    return render(request, 'bookings/my_bookings.html', {
        'upcoming': upcoming, 'past': past, 'cancelled': cancelled,
    })

@throttle_post('find-booking', limit=10)
def find_booking(request):
    form = FindBookingForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        pnr, phone = form.cleaned_data['pnr'], form.cleaned_data['phone']
        if limit_reached(request, 'find-pnr', limit=5, window=900, ident=pnr):
            form.add_error(None, 'Too many attempts for this PNR. Please try again in 15 minutes.')
        else:
            booking = Booking.objects.filter(pnr=pnr, contact_phone=phone).first()
            if booking:
                grant_access(request, booking)
                return redirect('bookings:detail', pnr=booking.pnr)
            form.add_error(None, 'No booking found with this PNR and mobile number.')
    return render(request, 'bookings/find_booking.html', {'form': form})


def cancel_booking(request, pnr):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'schedule__bus__operator', 'schedule__route', 'boarding_point', 'dropping_point'),
        pnr=pnr)
    if not can_view(request, booking):
        messages.error(request, 'You do not have access to this booking.')
        return redirect('core:home')
    if booking.booking_status != 'CONFIRMED':
        messages.error(request, 'Only confirmed bookings can be cancelled.')
        return redirect('bookings:detail', pnr=booking.pnr)

    policy = refund_policy(booking)
    if not policy['allowed']:
        messages.error(request, 'Cancellation is closed (less than 2 hours before departure).')
        return redirect('bookings:detail', pnr=booking.pnr)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()[:200]
        with transaction.atomic():
            booking.passengers.update(is_cancelled=True)  # frees the seats
            booking.booking_status = 'CANCELLED'
            if booking.payment_status == 'PAID':
                booking.payment_status = 'REFUNDED'
                booking.payments.filter(status='SUCCESS').update(status='REFUNDED')
            booking.save()
            Cancellation.objects.create(
                booking=booking, reason=reason, refund_amount=policy['refund'])
        messages.success(request, 'Your booking has been cancelled.')
        return redirect('bookings:detail', pnr=booking.pnr)

    return render(request, 'bookings/cancel_booking.html', {
        'booking': booking,
        'schedule': booking.schedule,
        'passengers': booking.passengers.select_related('seat'),
        'policy': policy,
    })

@never_cache
def access_booking(request, token):
    """Open a booking from a secure emailed link, in any browser."""
    if limit_reached(request, 'booking-link', limit=30, window=300):
        messages.error(request, 'Too many attempts. Please wait a few minutes and try again.')
        return redirect('core:home')
    booking = booking_for_token(token)
    if booking is None:
        messages.error(request, 'This link is invalid or has expired. '
                                'Find your booking with your PNR and mobile number.')
        return redirect('bookings:find')
    grant_access(request, booking)
    return redirect('bookings:detail', pnr=booking.pnr)


def pdf_response(booking, inline=False):
    response = HttpResponse(build_ticket_pdf(booking), content_type='application/pdf')
    disposition = 'inline' if inline else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{ticket_filename(booking)}"'
    response['Cache-Control'] = 'private, no-store'
    return response


@never_cache
def ticket_pdf(request, pnr):
    booking = get_object_or_404(
        Booking.objects.select_related(
            'schedule__bus__operator', 'schedule__route', 'boarding_point', 'dropping_point'),
        pnr=pnr)
    if not can_view(request, booking):
        messages.error(request, 'You do not have access to this ticket.')
        return redirect('core:home')
    if booking.booking_status != 'CONFIRMED':
        messages.warning(request, 'The e-ticket is available after the booking is confirmed.')
        return redirect('bookings:detail', pnr=booking.pnr)
    return pdf_response(booking, inline=request.GET.get('view') == '1')


@never_cache
def access_pdf(request, token):
    """Download the PDF from a secure emailed or SMS link, in any browser."""
    if limit_reached(request, 'booking-link', limit=30, window=300):
        messages.error(request, 'Too many attempts. Please wait a few minutes and try again.')
        return redirect('core:home')
    booking = booking_for_token(token)
    if booking is None:
        messages.error(request, 'This link is invalid or has expired. '
                                'Find your booking with your PNR and mobile number.')
        return redirect('bookings:find')
    if booking.booking_status != 'CONFIRMED':
        grant_access(request, booking)
        messages.warning(request, 'The e-ticket is available after the booking is confirmed.')
        return redirect('bookings:detail', pnr=booking.pnr)
    return pdf_response(booking)


@never_cache
def verify_ticket(request, uid):
    """Public page opened by scanning the QR code. No passenger data unless an admin is logged in."""
    if limit_reached(request, 'verify', limit=60, window=300):
        return render(request, 'bookings/verify.html', {'throttled': True}, status=429)
    booking = (Booking.objects
               .select_related('schedule__route', 'schedule__bus__operator')
               .filter(ticket_uid=uid).first())
    if booking is None:
        raise Http404('Unknown ticket')
    context = {
        'booking': booking,
        'schedule': booking.schedule,
        'valid': booking.booking_status == 'CONFIRMED',
        'seat_count': booking.passengers.filter(is_cancelled=False).count(),
    }
    if request.user.is_authenticated and request.user.is_staff:
        context['passengers'] = booking.passengers.select_related('seat')
    return render(request, 'bookings/verify.html', context)