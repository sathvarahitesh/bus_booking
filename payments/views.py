from datetime import timedelta

from django.contrib import messages
from bookings.services import confirm_booking
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from bookings.models import Booking
from bookings.utils import HOLD_MINUTES, can_view, get_customer, release_expired_holds

from .forms import PaymentForm
from .models import Payment

METHOD_ICONS = {
    'UPI': 'bi-phone',
    'CARD': 'bi-credit-card-2-front',
    'NETBANKING': 'bi-bank',
    'COUNTER': 'bi-cash-coin',
}


def pay(request, pnr):
    release_expired_holds()
    booking = get_object_or_404(
        Booking.objects.select_related('schedule__bus__operator', 'schedule__route'), pnr=pnr)
    if not can_view(request, booking):
        messages.error(request, 'You do not have access to this booking.')
        return redirect('core:home')

    # Logging in at payment time links the guest booking to the account
    customer = get_customer(request)
    if customer and booking.user_id is None:
        booking.user = customer
        booking.save(update_fields=['user'])

    if booking.booking_status != 'PENDING':
        if booking.booking_status == 'CANCELLED':
            messages.error(request, 'This booking has expired or was cancelled. Please book again.')
        return redirect('bookings:detail', pnr=booking.pnr)

    form = PaymentForm(request.POST or None)
    selected_method = request.POST.get('method') or 'UPI'

    if request.method == 'POST':
        if request.POST.get('action') == 'fail':
            # Demo only: record a failed attempt, keep the seats held
            method = selected_method if selected_method in dict(Payment.METHODS) else 'UPI'
            Payment.objects.create(booking=booking, method=method,
                                   amount=booking.total_amount, status='FAILED')
            messages.error(request, 'Payment failed (demo). Please try again.')
            return redirect('payments:pay', pnr=booking.pnr)

        if form.is_valid():
            confirmed, _payment = confirm_booking(booking, form.cleaned_data['method'])
            if confirmed:
                messages.success(request, 'Booking confirmed! Your e-ticket is ready.')
            elif booking.booking_status == 'CONFIRMED':
                messages.info(request, 'This booking is already confirmed.')
            else:
                messages.error(request, 'This booking has expired. Please book again.')
                return redirect('bookings:detail', pnr=booking.pnr)
            return redirect('bookings:ticket', pnr=booking.pnr)

    expires_at = booking.created_at + timedelta(minutes=HOLD_MINUTES)
    remaining = max(0, int((expires_at - timezone.now()).total_seconds())) + 1

    return render(request, 'payments/pay.html', {
        'booking': booking,
        'schedule': booking.schedule,
        'passengers': booking.passengers.select_related('seat'),
        'form': form,
        'methods': [(code, label, METHOD_ICONS[code]) for code, label in Payment.METHODS],
        'selected_method': selected_method,
        'remaining_seconds': remaining,
        'customer': customer,
    })