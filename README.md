# YatraBus: Online Bus Reservation and Ticket Booking System

MCA final-year project. A web-based bus booking platform built with **Django 5**, **Bootstrap 5** and **SQLite**.
Customers can search buses, pick seats, book **as a guest or with an account**, pay (demo) and print an e-ticket.
Admins manage everything from a separate dashboard.

## Features
**Customer:** search with filters (bus type, AC, seater/sleeper, time, price) · seat map (female/male booked colours) · boarding/dropping points · passenger details · demo payment (UPI, card, net banking, pay at counter) · e-ticket with print · booking history · check booking by PNR + mobile · cancellation with refund rules · optional login/registration and profile.

**Admin (`/dashboard/`):** statistics and revenue chart · CRUD for customers, operators, buses, routes, boarding/dropping points, schedules, seats, bookings, cancellations, payments · bulk schedule creation · reports with CSV export.

**Security:** CSRF, authentication, server-side validation, atomic seat booking with a database constraint (no double booking), 10-minute seat hold, staff-only admin, separate admin/customer logins, login throttling, session access control for guest bookings.

## Setup (Windows 11, PowerShell, Python 3.12+)
```powershell
cd bus_booking
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_data
python manage.py createsuperuser
python manage.py runserver
```
If activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

| Page | URL |
|---|---|
| Customer site | http://127.0.0.1:8000/ |
| Admin dashboard | http://127.0.0.1:8000/dashboard/ |
| Django admin (backup) | http://127.0.0.1:8000/admin/ |

Sample customer: `demo` / `demo12345`. Admin: the account you create with `createsuperuser`.

## Useful commands
```powershell
python manage.py seed_data                  # sample operators, buses, routes, 30 days of trips
python manage.py extend_schedules --days 60 # repeat trips for the next 60 days (run monthly)
python manage.py test                       # run automated tests
```

## Demo payment
No real payment is processed. Test card `4111 1111 1111 1111`, any future expiry, any 3-digit CVV. Test UPI `demo@upi`. Card details are never stored.

## Project structure
```
bus_booking/   settings, root urls
core/          home page, throttle helper
accounts/      custom user, login, register, profile
buses/         operators, buses, seats, routes, points, schedules, search, seat selection
bookings/      booking, passengers, cancellation, history, e-ticket
payments/      demo payment
dashboard/     admin panel, CRUD engine, reports
templates/  static/  docs/
```

## Environment variables (optional, for deployment)
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG` (`True`/`False`), `DJANGO_ALLOWED_HOSTS`, `DJANGO_HTTPS` (`1` when served over HTTPS).
Example: `$env:DJANGO_DEBUG="False"; python manage.py runserver --insecure`

## Documentation
See `docs/PROJECT_DOCUMENTATION.md`.