from datetime import time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from buses.models import BoardingPoint, Bus, BusOperator, BusSchedule, DroppingPoint, Route

from .models import Booking, Cancellation, Passenger
from .utils import refund_policy, release_expired_holds


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

    def passenger_data(self, seat, phone='9876543210'):
        return {f's{seat.pk}-name': 'Asha Patel', f's{seat.pk}-age': '28',
                f's{seat.pk}-gender': 'F', 'phone': phone, 'email': ''}

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