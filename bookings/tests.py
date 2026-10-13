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

    def make_booking(self, seats, schedule=None, user=None, status='CONFIRMED',
                     payment='PAID', phone='9876543210'):
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