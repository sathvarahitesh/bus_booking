import secrets
import string
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from buses.models import BoardingPoint, BusSchedule, DroppingPoint, Seat


class Booking(models.Model):
    BOOKING_STATUS = [
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('CANCELLED', 'Cancelled'),
    ]
    PAYMENT_STATUS = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]

    pnr = models.CharField(max_length=12, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='bookings')
    schedule = models.ForeignKey(BusSchedule, on_delete=models.PROTECT, related_name='bookings')
    boarding_point = models.ForeignKey(BoardingPoint, on_delete=models.PROTECT)
    dropping_point = models.ForeignKey(DroppingPoint, on_delete=models.PROTECT)
    contact_phone = models.CharField(max_length=15)
    contact_email = models.EmailField(blank=True)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    booking_status = models.CharField(max_length=10, choices=BOOKING_STATUS, default='PENDING')
    payment_status = models.CharField(max_length=10, choices=PAYMENT_STATUS, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    ticket_uid = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    access_token_hash = models.CharField(max_length=64, blank=True, default='',
                                         db_index=True, editable=False)
    access_expires_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.pnr

    def save(self, *args, **kwargs):
        if not self.pnr:
            while True:
                pnr = 'PNR' + ''.join(secrets.choice(string.digits) for _ in range(8))
                if not Booking.objects.filter(pnr=pnr).exists():
                    self.pnr = pnr
                    break

        if self.ticket_uid is None:
            self.ticket_uid = uuid.uuid4()

        super().save(*args, **kwargs)

    def ensure_ticket_uid(self):
        """Older bookings may have no ticket id yet; create it on first use."""
        if self.ticket_uid is None:
            self.ticket_uid = uuid.uuid4()
            self.save(update_fields=['ticket_uid'])
        return self.ticket_uid



class Passenger(models.Model):
    GENDER_CHOICES = [('M', 'Male'), ('F', 'Female'), ('O', 'Other')]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='passengers')
    # Copied from booking so the database can enforce "one active booking per seat per trip"
    schedule = models.ForeignKey(BusSchedule, on_delete=models.PROTECT, related_name='passengers',
                                 editable=False)
    seat = models.ForeignKey(Seat, on_delete=models.PROTECT, related_name='passengers')
    name = models.CharField(max_length=100)
    age = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(120)])
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    is_cancelled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['schedule', 'seat'],
                                    condition=Q(is_cancelled=False),
                                    name='unique_active_seat_per_schedule'),
        ]

    def __str__(self):
        return f'{self.name} - {self.seat.seat_number}'

    def save(self, *args, **kwargs):
        if not self.schedule_id:
            self.schedule = self.booking.schedule

    

        super().save(*args, **kwargs)


class Cancellation(models.Model):
    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name='cancellation')
    reason = models.TextField(blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cancelled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-cancelled_at']

    def __str__(self):
        return f'Cancellation of {self.booking.pnr}'


class Notification(models.Model):
    """One email or SMS per booking and kind. The unique constraint blocks duplicate sends."""
    CHANNELS = [('EMAIL', 'Email'), ('SMS', 'SMS')]
    KINDS = [('CONFIRMATION', 'Booking confirmation')]
    STATUSES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
        ('UNAVAILABLE', 'Not configured'),
        ('SIMULATED', 'Simulated (not delivered)'),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=5, choices=CHANNELS)
    kind = models.CharField(max_length=15, choices=KINDS, default='CONFIRMATION')
    status = models.CharField(max_length=12, choices=STATUSES, default='PENDING')
    recipient = models.CharField(max_length=254, blank=True)
    detail = models.TextField(blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['booking', 'channel', 'kind'],
                                    name='unique_notification_per_booking'),
        ]

    def __str__(self):
        return f'{self.booking.pnr} {self.channel} {self.status}'