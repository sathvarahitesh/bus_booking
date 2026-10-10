import secrets

from django.db import models

from bookings.models import Booking


class Payment(models.Model):
    METHODS = [
        ('UPI', 'UPI'),
        ('CARD', 'Credit/Debit Card'),
        ('NETBANKING', 'Net Banking'),
        ('COUNTER', 'Cash / Pay at Counter'),
    ]
    STATUSES = [
        ('PENDING', 'Pending'),
        ('SUCCESS', 'Success'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='payments')
    method = models.CharField(max_length=12, choices=METHODS)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default='PENDING')
    transaction_id = models.CharField(max_length=20, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.transaction_id} ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = 'TXN' + secrets.token_hex(6).upper()
        super().save(*args, **kwargs)