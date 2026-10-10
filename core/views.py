from django.shortcuts import render
from buses.models import Route

CITIES = [
    'Ahmedabad', 'Surat', 'Vadodara', 'Rajkot', 'Mumbai', 'Pune',
    'Jaipur', 'Udaipur', 'Delhi', 'Bengaluru', 'Chennai', 'Hyderabad',
]

POPULAR_ROUTES = [
    ('Ahmedabad', 'Mumbai'),
    ('Ahmedabad', 'Pune'),
    ('Surat', 'Mumbai'),
    ('Mumbai', 'Pune'),
    ('Delhi', 'Jaipur'),
    ('Jaipur', 'Udaipur'),
    ('Bengaluru', 'Chennai'),
    ('Hyderabad', 'Bengaluru'),
]

FEATURES = [
    ('bi-search', 'Easy Search', 'Find buses by route, date, type and price in seconds.'),
    ('bi-grid-3x3-gap', 'Choose Your Seat', 'Pick your preferred seat from a live seat layout.'),
    ('bi-shield-check', 'Secure Booking', 'Protected accounts and double-booking prevention.'),
    ('bi-credit-card', 'Demo Payments', 'UPI, card, net banking or pay at counter (simulated).'),
    ('bi-ticket-perforated', 'Instant E-Ticket', 'View and print your ticket right after booking.'),
    ('bi-arrow-counterclockwise', 'Easy Cancellation', 'Cancel from your booking history anytime.'),
]


def home(request):
    sources = Route.objects.values_list('source', flat=True)
    destinations = Route.objects.values_list('destination', flat=True)
    context = {
        'cities': sorted(set(sources) | set(destinations)),
        'popular_routes': POPULAR_ROUTES,
        'features': FEATURES,
    }
    return render(request, 'core/home.html', context)