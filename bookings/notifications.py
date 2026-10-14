import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Notification
from .services import access_url, issue_access_token, pdf_access_url
from .sms import send_sms
from .ticket import build_ticket_pdf, mask_email_address, mask_phone, ticket_data, ticket_filename

logger = logging.getLogger(__name__)

MAX_TRIES = 3
SIMULATED_MARKERS = ('console', 'locmem', 'memory', 'dummy', 'filebased')

# mode -> (statuses that may be sent, tries allowed before this one or None for no limit)
MODES = {
    'auto': (['PENDING'], 1),     # first send: each channel at most once
    'retry': (['FAILED'], MAX_TRIES),
    'force': (['PENDING', 'SENT', 'FAILED', 'UNAVAILABLE', 'SIMULATED'], None),  # admin resend
}


def email_address(booking):
    return booking.contact_email or (booking.user.email if booking.user_id else '')


def email_backend_is_simulated():
    backend = getattr(settings, 'MAILERS', {}).get('default', {}).get('BACKEND', '').lower()
    return any(marker in backend for marker in SIMULATED_MARKERS)


def sms_text(context):
    return (f"{context['brand']}: Booking confirmed. PNR {context['pnr']}. "
            f"{context['source']} to {context['destination']}, {context['travel_date']} "
            f"{context['departure_time']}. Seats {context['seats']}. Ticket: {context['ticket_link']}")


def _context(booking, token):
    data = ticket_data(booking)
    data['seats'] = ', '.join(p['seat'] for p in data['passengers'])
    data['customer_name'] = data['passengers'][0]['name'].split()[0] if data['passengers'] else 'Traveller'
    data['ticket_link'] = pdf_access_url(token)
    data['booking_link'] = access_url(token)
    data['link_expires'] = timezone.localtime(booking.access_expires_at).strftime('%d %b %Y')
    return data


def _send_email(booking, context):
    address = email_address(booking)
    if not address:
        return 'UNAVAILABLE', 'No email address on this booking.'
    message = EmailMultiAlternatives(
        subject=f"Booking confirmed: {context['source']} to {context['destination']} (PNR {context['pnr']})",
        body=render_to_string('emails/booking_confirmation.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[address],
    )
    message.attach_alternative(render_to_string('emails/booking_confirmation.html', context), 'text/html')
    message.attach(ticket_filename(booking), build_ticket_pdf(booking), 'application/pdf')
    message.send()
    if email_backend_is_simulated():
        return 'SIMULATED', 'Development email backend: the message was printed or stored, not delivered.'
    return 'SENT', 'Accepted by the mail server. Delivery to the inbox is not confirmed here.'


def _send_sms(booking, context):
    if not booking.contact_phone:
        return 'UNAVAILABLE', 'No mobile number on this booking.'
    return send_sms(booking.contact_phone, sms_text(context))


def _claim(notification, statuses, max_tries):
    """Atomically take the right to send. Returns False when another run already has it."""
    queryset = Notification.objects.filter(
        pk=notification.pk, status__in=statuses, attempts=notification.attempts)
    if max_tries is not None:
        queryset = queryset.filter(attempts__lt=max_tries)
    if not queryset.update(attempts=F('attempts') + 1):
        return False
    notification.refresh_from_db()
    return True


def _record(notification, status, detail):
    Notification.objects.filter(pk=notification.pk).update(
        status=status, detail=detail[:500], sent_at=timezone.now() if status == 'SENT' else None)


def _deliver(notification, action):
    """Run one send and record the outcome. Never raises and never touches the booking."""
    try:
        status, detail = action()
    except Exception as error:
        logger.exception('Notification %s failed', notification.pk)
        status, detail = 'FAILED', f'{type(error).__name__}: {str(error)[:200]}'
    _record(notification, status, detail)


def send_confirmation(booking, mode='auto'):
    """Send the confirmation email and SMS for a confirmed booking.

    auto  = first send; every channel is sent at most once, so a retried confirmation never duplicates it
    retry = send the channels that FAILED again (up to 3 tries)
    force = send both again (admin resend); new secure links are created and older ones stop working
    Never raises and never changes the booking or payment status.
    """
    if booking.booking_status != 'CONFIRMED':
        return []
    statuses, max_tries = MODES[mode]

    notes = {}
    for channel, recipient in (('EMAIL', mask_email_address(email_address(booking))),
                               ('SMS', mask_phone(booking.contact_phone))):
        notes[channel], _created = Notification.objects.get_or_create(
            booking=booking, channel=channel, kind='CONFIRMATION', defaults={'recipient': recipient})

    todo = [channel for channel, note in notes.items() if _claim(note, statuses, max_tries)]
    if todo:
        try:
            token = issue_access_token(booking)  # one secure link, shared by the email and the SMS
            context = _context(booking, token)
        except Exception as error:
            logger.exception('Could not prepare messages for booking %s', booking.pk)
            for channel in todo:
                _record(notes[channel], 'FAILED', f'Could not prepare the message: {type(error).__name__}')
        else:
            if 'EMAIL' in todo:
                _deliver(notes['EMAIL'], lambda: _send_email(booking, context))
            if 'SMS' in todo:
                _deliver(notes['SMS'], lambda: _send_sms(booking, context))
    return list(booking.notifications.all())


def notify_confirmed(booking):
    """Called right after a payment. A messaging problem must never break the booking flow."""
    try:
        send_confirmation(booking, mode='auto')
    except Exception:
        logger.exception('Confirmation messages failed for booking %s', booking.pk)