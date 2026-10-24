from datetime import time, timedelta
from decimal import Decimal
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone


from buses.models import BoardingPoint, Bus, BusOperator, BusSchedule, DroppingPoint, Route

from payments.models import Payment
from .models import Booking, Cancellation, Notification, Passenger
from .utils import refund_policy, release_expired_holds

import hashlib
from accounts.models import User
from .services import confirm_booking, issue_access_token

import uuid

from django.conf import settings

from .ticket import build_ticket_pdf, ticket_data
import io
import re
from base64 import b64encode
from contextlib import redirect_stdout
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs

from django.core import mail
from django.core.management import call_command
from django.test import override_settings

from .notifications import notify_confirmed, send_confirmation, sms_text

class TripTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        operator = BusOperator.objects.create(name='Test Travels')
        cls.bus = Bus.objects.create(
            operator=operator, bus_number='TS-01-AA-0001', bus_type='volvo', is_ac=True,
            seat_type='seater', total_seats=8, seat_price=Decimal('500'))
        cls.bus.generate_seats()
        cls.route = Route.objects.create(
            source='Alpha', destination='Beta', distance_km=100, duration=timedelta(hours=3))
        cls.boarding = BoardingPoint.objects.create(route=cls.route, name='Stand A', minutes_before=15)
        cls.dropping = DroppingPoint.objects.create(route=cls.route, name='Stand B', minutes_before=0)
        cls.schedule = BusSchedule.objects.create(
            bus=cls.bus, route=cls.route,
            travel_date=timezone.localdate() + timedelta(days=3), departure_time=time(21, 0))
        cls.seats = list(cls.bus.seats.all())

    def setUp(self):
        cache.clear()
        patch = override_settings(SMS_PROVIDER='')  # tests must never send a real SMS
        patch.enable()
        self.addCleanup(patch.disable)

    def make_booking(self, seats, schedule=None, user=None, status='CONFIRMED',
                     payment='PAID', phone='9876543210', email='buyer@example.com'):
        booking = Booking.objects.create(
            user=user, schedule=schedule or self.schedule, boarding_point=self.boarding,
            dropping_point=self.dropping, contact_phone=phone, contact_email=email,
            total_amount=self.bus.seat_price * len(seats),
            booking_status=status, payment_status=payment)
        for seat in seats:
            Passenger.objects.create(booking=booking, seat=seat, name='Test Person', age=30, gender='F')
        return booking

    def make_booking_with_details(self, seats, schedule=None, user=None, status='CONFIRMED',
                                  payment='PAID', phone='9876543210', email='buyer@example.com'):
        booking = Booking.objects.create(
            user=user, schedule=schedule or self.schedule, boarding_point=self.boarding,
            dropping_point=self.dropping, contact_phone=phone,
            total_amount=self.bus.seat_price * len(seats),
            booking_status=status, payment_status=payment)
        for seat in seats:
            Passenger.objects.create(booking=booking, seat=seat, name='Test Person', age=30, gender='F')
        return booking

    def allow(self, booking):
        session = self.client.session
        session['booking_access'] = [booking.pnr]
        session.save()

    def trip_in(self, hours):
        when = timezone.localtime().replace(tzinfo=None) + timedelta(hours=hours)
        return BusSchedule.objects.create(
            bus=self.bus, route=self.route, travel_date=when.date(), departure_time=when.time())


class ModelRuleTests(TripTestCase):
    def test_pnr_format(self):
        booking = self.make_booking([self.seats[0]])
        self.assertTrue(booking.pnr.startswith('PNR'))
        self.assertEqual(len(booking.pnr), 11)

    def test_seat_cannot_be_double_booked(self):
        self.make_booking([self.seats[0]])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_booking([self.seats[0]])

    def test_cancelled_seat_can_be_rebooked(self):
        first = self.make_booking([self.seats[0]])
        first.passengers.update(is_cancelled=True)
        self.make_booking([self.seats[0]])
        self.assertEqual(Passenger.objects.filter(is_cancelled=False).count(), 1)

    def test_expired_unpaid_hold_releases_seats(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        Booking.objects.filter(pk=booking.pk).update(created_at=timezone.now() - timedelta(minutes=11))
        release_expired_holds()
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CANCELLED')
        self.assertFalse(booking.passengers.filter(is_cancelled=False).exists())

    def test_refund_policy_early(self):
        policy = refund_policy(self.make_booking([self.seats[0]]))
        self.assertTrue(policy['allowed'])
        self.assertEqual(policy['percent'], 90)
        self.assertEqual(policy['refund'], Decimal('450.00'))

    def test_refund_policy_unpaid_gets_no_refund(self):
        policy = refund_policy(self.make_booking([self.seats[0]], payment='PENDING'))
        self.assertTrue(policy['allowed'])
        self.assertEqual(policy['refund'], Decimal('0'))

    def test_refund_policy_half(self):
        policy = refund_policy(self.make_booking([self.seats[0]], schedule=self.trip_in(13)))
        self.assertEqual(policy['percent'], 50)
        self.assertEqual(policy['refund'], Decimal('250.00'))

    def test_cancellation_closed_close_to_departure(self):
        policy = refund_policy(self.make_booking([self.seats[0]], schedule=self.trip_in(1)))
        self.assertFalse(policy['allowed'])


class SearchAndSeatTests(TripTestCase):
    def seat_post(self, seat_ids):
        return self.client.post(reverse('buses:seats', args=[self.schedule.pk]), {
            'seats': ','.join(str(i) for i in seat_ids),
            'boarding_point': self.boarding.pk,
            'dropping_point': self.dropping.pk,
        })

    def test_search_lists_bus(self):
        response = self.client.get(reverse('buses:search'), {
            'source': 'Alpha', 'destination': 'Beta', 'date': self.schedule.travel_date.isoformat()})
        self.assertContains(response, 'Test Travels')

    def test_search_rejects_same_city(self):
        response = self.client.get(reverse('buses:search'), {
            'source': 'Alpha', 'destination': 'alpha', 'date': self.schedule.travel_date.isoformat()})
        self.assertRedirects(response, reverse('core:home'))

    def test_seat_status_shows_gender(self):
        self.make_booking([self.seats[0]])
        data = self.client.get(reverse('buses:seat_status', args=[self.schedule.pk])).json()
        self.assertEqual(data['booked'], {str(self.seats[0].pk): 'F'})
        self.assertEqual(data['available'], 7)

    def test_free_seat_is_saved_in_session(self):
        response = self.seat_post([self.seats[1].pk])
        self.assertRedirects(response, reverse('bookings:passengers'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['booking_draft']['seat_ids'], [self.seats[1].pk])

    def test_booked_seat_is_rejected(self):
        self.make_booking([self.seats[0]])
        response = self.seat_post([self.seats[0].pk])
        self.assertRedirects(response, reverse('buses:seats', args=[self.schedule.pk]),
                             fetch_redirect_response=False)
        self.assertNotIn('booking_draft', self.client.session)

    def test_more_than_six_seats_rejected(self):
        self.seat_post([s.pk for s in self.seats[:7]])
        self.assertNotIn('booking_draft', self.client.session)


class BookingFlowTests(TripTestCase):
    def start_booking(self, seat):
        self.client.post(reverse('buses:seats', args=[self.schedule.pk]), {
            'seats': str(seat.pk), 'boarding_point': self.boarding.pk,
            'dropping_point': self.dropping.pk})

    def passenger_data(self, seat, phone='9876543210', email='asha@example.com'):
        return {f's{seat.pk}-name': 'Asha Patel', f's{seat.pk}-age': '28',
                f's{seat.pk}-gender': 'F', 'phone': phone, 'email': email}

    def test_guest_can_book_without_login(self):
        seat = self.seats[0]
        self.start_booking(seat)
        response = self.client.post(reverse('bookings:passengers'), self.passenger_data(seat))
        booking = Booking.objects.get()
        self.assertRedirects(response, reverse('bookings:detail', args=[booking.pnr]))
        self.assertIsNone(booking.user)
        self.assertEqual(booking.booking_status, 'PENDING')
        self.assertEqual(booking.total_amount, Decimal('500'))
        # a different browser cannot open it
        other = Client().get(reverse('bookings:detail', args=[booking.pnr]))
        self.assertRedirects(other, reverse('core:home'), fetch_redirect_response=False)

    def test_invalid_phone_is_rejected(self):
        seat = self.seats[0]
        self.start_booking(seat)
        response = self.client.post(reverse('bookings:passengers'), self.passenger_data(seat, phone='12345'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)

    def test_find_booking_needs_matching_phone(self):
        booking = self.make_booking([self.seats[0]])
        wrong = self.client.post(reverse('bookings:find'), {'pnr': booking.pnr, 'phone': '9000000000'})
        self.assertContains(wrong, 'No booking found')
        right = self.client.post(reverse('bookings:find'), {'pnr': booking.pnr, 'phone': '9876543210'})
        self.assertRedirects(right, reverse('bookings:detail', args=[booking.pnr]))

    def test_card_payment_confirms_booking(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        self.allow(booking)
        response = self.client.post(reverse('payments:pay', args=[booking.pnr]), {
            'method': 'CARD', 'card_number': '4111 1111 1111 1111', 'card_name': 'Test',
            'expiry': '12/99', 'cvv': '123'})
        self.assertRedirects(response, reverse('bookings:ticket', args=[booking.pnr]))
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')
        self.assertEqual(booking.payment_status, 'PAID')

    def test_invalid_card_keeps_booking_pending(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        self.allow(booking)
        response = self.client.post(reverse('payments:pay', args=[booking.pnr]), {
            'method': 'CARD', 'card_number': '1234', 'card_name': 'Test',
            'expiry': '12/99', 'cvv': '123'})
        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'PENDING')

    def test_pay_at_counter_confirms_with_payment_pending(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        self.allow(booking)
        self.client.post(reverse('payments:pay', args=[booking.pnr]), {'method': 'COUNTER'})
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')
        self.assertEqual(booking.payment_status, 'PENDING')

    def test_cancel_frees_seats_and_records_refund(self):
        booking = self.make_booking([self.seats[0]])
        self.allow(booking)
        self.client.post(reverse('bookings:cancel', args=[booking.pnr]), {'reason': 'Plan changed'})
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CANCELLED')
        self.assertEqual(booking.payment_status, 'REFUNDED')
        self.assertEqual(Cancellation.objects.get(booking=booking).refund_amount, Decimal('450.00'))
        self.assertFalse(booking.passengers.filter(is_cancelled=False).exists())


class FoundationTests(TripTestCase):
    def test_new_booking_gets_unique_ticket_uid(self):
        first = self.make_booking([self.seats[0]])
        second = self.make_booking([self.seats[1]])
        self.assertIsNotNone(first.ticket_uid)
        self.assertNotEqual(first.ticket_uid, second.ticket_uid)

    def test_confirm_booking_is_idempotent(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        done, payment = confirm_booking(booking, 'UPI')
        again, second_payment = confirm_booking(booking, 'UPI')
        self.assertTrue(done)
        self.assertFalse(again)
        self.assertIsNone(second_payment)
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')
        self.assertEqual(booking.payment_status, 'PAID')
        self.assertEqual(payment.status, 'SUCCESS')
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 1)

    def test_pay_at_counter_keeps_payment_pending(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        done, payment = confirm_booking(booking, 'COUNTER')
        self.assertTrue(done)
        self.assertEqual(payment.status, 'PENDING')
        booking.refresh_from_db()
        self.assertEqual(booking.booking_status, 'CONFIRMED')
        self.assertEqual(booking.payment_status, 'PENDING')

    def test_cancelled_booking_cannot_be_confirmed(self):
        booking = self.make_booking([self.seats[0]], status='CANCELLED', payment='FAILED')
        done, payment = confirm_booking(booking, 'UPI')
        self.assertFalse(done)
        self.assertIsNone(payment)
        self.assertEqual(Payment.objects.filter(booking=booking).count(), 0)

    def test_one_notification_per_booking_channel_and_kind(self):
        booking = self.make_booking([self.seats[0]])
        Notification.objects.create(booking=booking, channel='EMAIL')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Notification.objects.create(booking=booking, channel='EMAIL')
        Notification.objects.create(booking=booking, channel='SMS')  # other channel is allowed
        self.assertEqual(booking.notifications.count(), 2)



class GuestAccessTests(TripTestCase):
    def start_booking(self, seat):
        self.client.post(reverse('buses:seats', args=[self.schedule.pk]), {
            'seats': str(seat.pk), 'boarding_point': self.boarding.pk,
            'dropping_point': self.dropping.pk})

    def test_guest_email_is_required(self):
        seat = self.seats[0]
        self.start_booking(seat)
        response = self.client.post(reverse('bookings:passengers'), {
            f's{seat.pk}-name': 'Asha Patel', f's{seat.pk}-age': '28', f's{seat.pk}-gender': 'F',
            'phone': '9876543210', 'email': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)

    def test_secure_link_opens_the_booking_in_a_new_browser(self):
        booking = self.make_booking([self.seats[0]])
        token = issue_access_token(booking)
        other = Client()
        response = other.get(reverse('bookings:access', args=[token]))
        self.assertRedirects(response, reverse('bookings:detail', args=[booking.pnr]))

    def test_invalid_or_expired_link_is_refused(self):
        booking = self.make_booking([self.seats[0]])
        bad = self.client.get(reverse('bookings:access', args=['not-a-real-token']))
        self.assertRedirects(bad, reverse('bookings:find'))
        token = issue_access_token(booking)
        Booking.objects.filter(pk=booking.pk).update(
            access_expires_at=timezone.now() - timedelta(minutes=1))
        expired = self.client.get(reverse('bookings:access', args=[token]))
        self.assertRedirects(expired, reverse('bookings:find'))

    def test_a_new_link_replaces_the_old_one(self):
        booking = self.make_booking([self.seats[0]])
        first = issue_access_token(booking)
        second = issue_access_token(booking)
        old = Client().get(reverse('bookings:access', args=[first]))
        self.assertRedirects(old, reverse('bookings:find'))
        new = Client().get(reverse('bookings:access', args=[second]))
        self.assertRedirects(new, reverse('bookings:detail', args=[booking.pnr]))

    def test_only_the_hash_of_the_token_is_stored(self):
        booking = self.make_booking([self.seats[0]])
        token = issue_access_token(booking)
        booking.refresh_from_db()
        self.assertNotEqual(booking.access_token_hash, token)
        self.assertEqual(booking.access_token_hash, hashlib.sha256(token.encode()).hexdigest())

    def test_pnr_lookup_is_limited_per_booking(self):
        booking = self.make_booking([self.seats[0]])
        for _ in range(5):
            self.client.post(reverse('bookings:find'), {'pnr': booking.pnr, 'phone': '9000000000'})
        response = self.client.post(
            reverse('bookings:find'), {'pnr': booking.pnr, 'phone': '9876543210'})
        self.assertContains(response, 'Too many attempts')

    def test_guest_choice_is_shown_only_to_guests(self):
        self.start_booking(self.seats[0])
        guest_page = self.client.get(reverse('bookings:passengers'))
        self.assertContains(guest_page, 'Continue as Guest')
        user = User.objects.create_user('c@example.com', 'c@example.com', 'Str0ng#Pass99')
        self.client.force_login(user)
        member_page = self.client.get(reverse('bookings:passengers'))
        self.assertNotContains(member_page, 'Continue as Guest')


class PDFTicketTests(TripTestCase):
    def confirmed(self, count=1, **kwargs):
        return self.make_booking(self.seats[:count], **kwargs)

    def test_ticket_data_comes_from_the_database(self):
        booking = self.confirmed(2)
        data = ticket_data(booking)
        self.assertEqual(data['pnr'], booking.pnr)
        self.assertEqual([p['seat'] for p in data['passengers']],
                         [s.seat_number for s in self.seats[:2]])
        self.assertEqual(data['total'], '1,000.00')
        self.assertEqual((data['source'], data['destination']), ('Alpha', 'Beta'))
        self.assertEqual(data['status'], 'Confirmed')
        self.assertTrue(data['verify_url'].startswith(settings.SITE_URL))
        self.assertIn(str(booking.ticket_uid), data['verify_url'])
        self.assertNotEqual(data['contact_phone'], '9876543210')
        self.assertTrue(data['contact_phone'].startswith('98'))

    def test_pdf_is_a_valid_pdf(self):
        pdf = build_ticket_pdf(self.confirmed(3))
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertIn(b'%%EOF', pdf[-32:])

    def test_six_passengers_build_too(self):
        self.assertTrue(build_ticket_pdf(self.confirmed(6)).startswith(b'%PDF'))

    def test_owner_session_can_download(self):
        booking = self.confirmed()
        self.allow(booking)
        response = self.client.get(reverse('bookings:ticket_pdf', args=[booking.pnr]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn(booking.pnr, response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF'))

    def test_registered_owner_can_download(self):
        user = User.objects.create_user('o@example.com', 'o@example.com', 'Str0ng#Pass99')
        booking = self.make_booking([self.seats[0]], user=user)
        self.client.force_login(user)
        response = self.client.get(reverse('bookings:ticket_pdf', args=[booking.pnr]))
        self.assertEqual(response.status_code, 200)

    def test_stranger_cannot_download(self):
        booking = self.confirmed()
        response = Client().get(reverse('bookings:ticket_pdf', args=[booking.pnr]))
        self.assertRedirects(response, reverse('core:home'), fetch_redirect_response=False)

    def test_pending_booking_has_no_pdf(self):
        booking = self.make_booking([self.seats[0]], status='PENDING', payment='PENDING')
        self.allow(booking)
        response = self.client.get(reverse('bookings:ticket_pdf', args=[booking.pnr]))
        self.assertRedirects(response, reverse('bookings:detail', args=[booking.pnr]),
                             fetch_redirect_response=False)

    def test_secure_link_downloads_the_pdf_in_a_new_browser(self):
        booking = self.confirmed()
        token = issue_access_token(booking)
        response = Client().get(reverse('bookings:access_pdf', args=[token]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_bad_secure_link_is_refused(self):
        response = Client().get(reverse('bookings:access_pdf', args=['not-a-real-token']))
        self.assertRedirects(response, reverse('bookings:find'))

    def test_verify_page_is_public_and_hides_passenger_data(self):
        booking = self.confirmed()
        response = Client().get(reverse('bookings:verify', args=[booking.ticket_uid]))
        self.assertContains(response, 'VALID TICKET')
        self.assertContains(response, booking.pnr)
        self.assertNotContains(response, 'Test Person')
        self.assertNotContains(response, '9876543210')

    def test_staff_see_passenger_names_on_verify_page(self):
        staff = User.objects.create_user('boss@example.com', 'boss@example.com',
                                         'Str0ng#Pass99', is_staff=True)
        booking = self.confirmed()
        client = Client()
        client.force_login(staff)
        response = client.get(reverse('bookings:verify', args=[booking.ticket_uid]))
        self.assertContains(response, 'Test Person')

    def test_cancelled_ticket_is_not_valid(self):
        booking = self.make_booking([self.seats[0]], status='CANCELLED', payment='REFUNDED')
        response = Client().get(reverse('bookings:verify', args=[booking.ticket_uid]))
        self.assertContains(response, 'NOT VALID')

    def test_unknown_ticket_id_is_404(self):
        response = Client().get(reverse('bookings:verify', args=[uuid.uuid4()]))
        self.assertEqual(response.status_code, 404)



class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def parts_of(message):
    return {part.get_content_type(): part for part in message.message().walk()}


def text_of(message):
    return parts_of(message)['text/plain'].get_payload(decode=True).decode()


class NotificationTests(TripTestCase):
    def booking(self, **kwargs):
        return self.make_booking([self.seats[0]], **kwargs)

    def test_email_has_text_html_pdf_and_link(self):
        booking = self.booking()
        send_confirmation(booking)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['buyer@example.com'])
        self.assertIn(booking.pnr, message.subject)
        parts = parts_of(message)
        self.assertIn('text/html', parts)
        self.assertTrue(parts['application/pdf'].get_payload(decode=True).startswith(b'%PDF'))
        text = text_of(message)
        self.assertIn(booking.pnr, text)
        self.assertIn(f'Seats: {self.seats[0].seat_number}', text)
        self.assertRegex(text, r'/bookings/access/\S+/pdf/')
        self.assertIn('&#8377;500.00', parts['text/html'].get_payload(decode=True).decode())

    def test_secure_link_in_email_downloads_the_pdf(self):
        booking = self.booking()
        send_confirmation(booking)
        link = re.search(r'(\S*/bookings/access/\S+/pdf/)', text_of(mail.outbox[0])).group(1)
        response = Client().get(link.replace(settings.SITE_URL, ''))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    def test_retry_does_not_send_twice(self):
        booking = self.booking()
        send_confirmation(booking)
        send_confirmation(booking)
        notify_confirmed(booking)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(set(Notification.objects.values_list('attempts', flat=True)), {1})

    def test_email_failure_is_recorded_and_booking_unchanged(self):
        booking = self.booking()
        with self.assertLogs('bookings.notifications', level='ERROR'):
            with mock.patch('bookings.notifications.EmailMultiAlternatives.send',
                            side_effect=OSError('smtp down')):
                send_confirmation(booking)
        note = booking.notifications.get(channel='EMAIL')
        self.assertEqual(note.status, 'FAILED')
        self.assertIn('smtp down', note.detail)
        booking.refresh_from_db()
        self.assertEqual((booking.booking_status, booking.payment_status), ('CONFIRMED', 'PAID'))
        self.assertEqual(len(mail.outbox), 0)
        send_confirmation(booking, mode='retry')  # only the failed channel is tried again
        self.assertEqual(len(mail.outbox), 1)
        note.refresh_from_db()
        self.assertIn(note.status, ('SENT', 'SIMULATED'))
        self.assertEqual(note.attempts, 2)
        self.assertEqual(booking.notifications.get(channel='SMS').attempts, 1)

    def test_retry_stops_after_three_tries(self):
        booking = self.booking()
        with self.assertLogs('bookings.notifications', level='ERROR'):
            with mock.patch('bookings.notifications.EmailMultiAlternatives.send',
                            side_effect=OSError('down')):
                for mode in ('auto', 'retry', 'retry', 'retry', 'retry'):
                    send_confirmation(booking, mode=mode)
        self.assertEqual(booking.notifications.get(channel='EMAIL').attempts, 3)

    def test_real_backend_is_reported_as_sent(self):
        booking = self.booking()
        with mock.patch('bookings.notifications.email_backend_is_simulated', return_value=False):
            send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='EMAIL').status, 'SENT')

    def test_development_backend_is_reported_as_simulated(self):
        booking = self.booking()
        send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='EMAIL').status, 'SIMULATED')

    def test_sms_unavailable_without_provider(self):
        booking = self.booking()
        send_confirmation(booking)
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'UNAVAILABLE')
        self.assertEqual(note.status_text, 'Not configured')
        self.assertEqual(len(mail.outbox), 1)  # the email still went out

    @override_settings(SMS_PROVIDER='console')
    def test_sms_console_is_simulated_not_sent(self):
        booking = self.booking()
        out = io.StringIO()
        with redirect_stdout(out):
            send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='SMS').status, 'SIMULATED')
        self.assertIn(booking.pnr, out.getvalue())

    @override_settings(SMS_PROVIDER='twilio', TWILIO_ACCOUNT_SID='ACtest',
                       TWILIO_AUTH_TOKEN='secret-token', TWILIO_FROM_NUMBER='+15550001111')
    def test_twilio_request_and_success(self):
        booking = self.booking()
        reply = FakeResponse(b'{"status": "queued", "sid": "SM1"}')
        with mock.patch('bookings.sms.urlopen', return_value=reply) as urlopen:
            send_confirmation(booking)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith('/2010-04-01/Accounts/ACtest/Messages.json'))
        form = parse_qs(request.data.decode())
        self.assertEqual(form['To'], ['+919876543210'])
        self.assertEqual(form['From'], ['+15550001111'])
        self.assertIn(booking.pnr, form['Body'][0])
        self.assertEqual(request.get_header('Authorization'),
                         'Basic ' + b64encode(b'ACtest:secret-token').decode())
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'SENT')
        self.assertNotIn('secret-token', note.detail)

    @override_settings(SMS_PROVIDER='twilio', TWILIO_ACCOUNT_SID='ACtest',
                       TWILIO_AUTH_TOKEN='secret-token', TWILIO_FROM_NUMBER='+15550001111')
    def test_twilio_error_is_recorded(self):
        booking = self.booking()
        error = HTTPError('http://x', 401, 'Unauthorized', {}, io.BytesIO(b'{"message": "Authenticate"}'))
        with mock.patch('bookings.sms.urlopen', side_effect=error):
            send_confirmation(booking)
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'FAILED')
        self.assertIn('401', note.detail)
        self.assertNotIn('secret-token', note.detail)

    @override_settings(SMS_PROVIDER='twilio', TWILIO_ACCOUNT_SID='ACtest',
                       TWILIO_AUTH_TOKEN='x', TWILIO_FROM_NUMBER='+15550001111')
    def test_twilio_network_error_is_recorded(self):
        booking = self.booking()
        with mock.patch('bookings.sms.urlopen', side_effect=TimeoutError('timed out')):
            send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='SMS').status, 'FAILED')

    @override_settings(SMS_PROVIDER='twilio', TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='',
                       TWILIO_FROM_NUMBER='')
    def test_twilio_without_credentials_is_unavailable(self):
        booking = self.booking()
        send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='SMS').status, 'UNAVAILABLE')

    def test_pending_booking_sends_nothing(self):
        booking = self.booking(status='PENDING', payment='PENDING')
        self.assertEqual(send_confirmation(booking), [])
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_missing_email_is_unavailable(self):
        booking = self.booking(email='')
        send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='EMAIL').status, 'UNAVAILABLE')
        self.assertEqual(len(mail.outbox), 0)

    def test_force_resend_sends_again_with_a_new_link(self):
        booking = self.booking()
        send_confirmation(booking)
        send_confirmation(booking, mode='force')
        self.assertEqual(len(mail.outbox), 2)
        links = [re.search(r'(\S*/bookings/access/\S+/pdf/)', text_of(m)).group(1) for m in mail.outbox]
        self.assertNotEqual(links[0], links[1])
        old = Client().get(links[0].replace(settings.SITE_URL, ''))
        self.assertRedirects(old, reverse('bookings:find'))

    def test_notify_confirmed_never_raises(self):
        booking = self.booking()
        with self.assertLogs('bookings.notifications', level='ERROR'):
            with mock.patch('bookings.notifications.send_confirmation', side_effect=RuntimeError('boom')):
                notify_confirmed(booking)

    def test_preparation_error_marks_failed(self):
        booking = self.booking()
        with self.assertLogs('bookings.notifications', level='ERROR'):
            with mock.patch('bookings.notifications.ticket_data', side_effect=KeyError('x')):
                send_confirmation(booking)
        self.assertEqual(set(booking.notifications.values_list('status', flat=True)), {'FAILED'})
        self.assertEqual(len(mail.outbox), 0)

    def test_payment_sends_confirmation_once(self):
        booking = self.booking(status='PENDING', payment='PENDING')
        self.allow(booking)
        url = reverse('payments:pay', args=[booking.pnr])
        data = {'method': 'UPI', 'upi_id': 'demo@upi'}
        self.client.post(url, data)
        self.client.post(url, data)  # a second submit finds the booking already confirmed
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(booking.notifications.count(), 2)

    def test_status_is_shown_on_ticket_and_booking_pages(self):
        booking = self.booking()
        send_confirmation(booking)
        self.allow(booking)
        for name in ('bookings:ticket', 'bookings:detail'):
            response = self.client.get(reverse(name, args=[booking.pnr]))
            self.assertContains(response, 'Confirmation messages')
            self.assertContains(response, 'Not configured')

    def test_dashboard_resend_by_staff(self):
        staff = User.objects.create_user('boss@example.com', 'boss@example.com',
                                         'Str0ng#Pass99', is_staff=True)
        booking = self.booking()
        self.client.force_login(staff)
        response = self.client.post(reverse('dashboard:resend_notifications', args=[booking.pk]))
        self.assertRedirects(response, reverse('dashboard:booking', args=[booking.pk]))
        self.assertEqual(len(mail.outbox), 1)

    def test_dashboard_resend_blocked_for_non_staff(self):
        booking = self.booking()
        response = self.client.post(reverse('dashboard:resend_notifications', args=[booking.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_command_retries_failed_messages(self):
        booking = self.booking()
        with self.assertLogs('bookings.notifications', level='ERROR'):
            with mock.patch('bookings.notifications.EmailMultiAlternatives.send',
                            side_effect=OSError('down')):
                send_confirmation(booking)
        call_command('send_notifications', stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 1)


class Fast2SMSTests(TripTestCase):
    def booking(self):
        return self.make_booking([self.seats[0]])

    @override_settings(SMS_PROVIDER='fast2sms', FAST2SMS_API_KEY='key-abc123')
    def test_request_and_success(self):
        booking = self.booking()
        reply = FakeResponse(
            b'{"return": true, "request_id": "req123", "message": ["Message sent successfully"]}')
        with mock.patch('bookings.sms.urlopen', return_value=reply) as urlopen:
            send_confirmation(booking)
        request = urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith('/dev/bulkV2'))
        self.assertEqual(request.get_header('Authorization'), 'key-abc123')
        body = json.loads(request.data.decode())
        self.assertEqual(body['route'], 'q')
        self.assertEqual(body['numbers'], '9876543210')
        self.assertIn(booking.pnr, body['message'])
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'SENT')
        self.assertIn('req123', note.detail)
        self.assertNotIn('key-abc123', note.detail)

    @override_settings(SMS_PROVIDER='fast2sms', FAST2SMS_API_KEY='key-abc123')
    def test_api_error_reply_is_recorded(self):
        booking = self.booking()
        reply = FakeResponse(b'{"return": false, "status_code": 412, "message": "Invalid key"}')
        with mock.patch('bookings.sms.urlopen', return_value=reply):
            send_confirmation(booking)
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'FAILED')
        self.assertIn('Invalid key', note.detail)

    @override_settings(SMS_PROVIDER='fast2sms', FAST2SMS_API_KEY='key-abc123')
    def test_http_error_is_recorded(self):
        booking = self.booking()
        error = HTTPError('http://x', 401, 'Unauthorized', {}, io.BytesIO(b'{"message": "Unauthorized"}'))
        with mock.patch('bookings.sms.urlopen', side_effect=error):
            send_confirmation(booking)
        note = booking.notifications.get(channel='SMS')
        self.assertEqual(note.status, 'FAILED')
        self.assertIn('401', note.detail)

    @override_settings(SMS_PROVIDER='fast2sms', FAST2SMS_API_KEY='')
    def test_missing_key_is_unavailable(self):
        booking = self.booking()
        send_confirmation(booking)
        self.assertEqual(booking.notifications.get(channel='SMS').status, 'UNAVAILABLE')

    def test_link_can_be_left_out_of_the_text(self):
        context = {'brand': 'YatraBus', 'pnr': 'PNR1', 'source': 'A', 'destination': 'B',
                   'travel_date': 'Mon', 'departure_time': '09:00 AM', 'seats': '1',
                   'ticket_link': 'http://x/y/'}
        self.assertIn('http://x/y/', sms_text(context))
        with override_settings(SMS_INCLUDE_LINK=False):
            self.assertNotIn('http', sms_text(context))