import re

from django import forms
from django.utils import timezone

from accounts.forms import BootstrapMixin

from .models import Payment

BANKS = [
    ('', 'Select your bank'),
    ('SBI', 'State Bank of India'),
    ('HDFC', 'HDFC Bank'),
    ('ICICI', 'ICICI Bank'),
    ('AXIS', 'Axis Bank'),
    ('PNB', 'Punjab National Bank'),
    ('BOB', 'Bank of Baroda'),
    ('KOTAK', 'Kotak Mahindra Bank'),
]


class PaymentForm(BootstrapMixin, forms.Form):
    method = forms.ChoiceField(choices=Payment.METHODS)
    upi_id = forms.CharField(required=False, label='UPI ID', max_length=60)
    card_name = forms.CharField(required=False, label='Name on card', max_length=60)
    card_number = forms.CharField(required=False, label='Card number', max_length=23)
    expiry = forms.CharField(required=False, label='Expiry (MM/YY)', max_length=5)
    cvv = forms.CharField(required=False, label='CVV', max_length=3,
                          widget=forms.PasswordInput(render_value=False))
    bank = forms.ChoiceField(choices=BANKS, required=False, label='Bank')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['upi_id'].widget.attrs['placeholder'] = 'name@upi'
        self.fields['card_number'].widget.attrs['placeholder'] = '1234 5678 9012 3456'
        self.fields['expiry'].widget.attrs['placeholder'] = 'MM/YY'

    def clean(self):
        data = super().clean()
        method = data.get('method')

        if method == 'UPI':
            if not re.fullmatch(r'[\w.\-]{2,}@[A-Za-z]{2,}', data.get('upi_id', '').strip()):
                self.add_error('upi_id', 'Enter a valid UPI ID, e.g. name@upi')

        elif method == 'CARD':
            number = re.sub(r'\s', '', data.get('card_number', ''))
            if not (number.isdigit() and len(number) == 16):
                self.add_error('card_number', 'Enter a valid 16-digit card number.')
            if not data.get('card_name', '').strip():
                self.add_error('card_name', 'Enter the name on the card.')

            match = re.fullmatch(r'(\d{2})/(\d{2})', data.get('expiry', '').strip())
            valid_expiry = False
            if match:
                month, year = int(match.group(1)), 2000 + int(match.group(2))
                today = timezone.localdate()
                valid_expiry = 1 <= month <= 12 and (year, month) >= (today.year, today.month)
            if not valid_expiry:
                self.add_error('expiry', 'Enter a valid future expiry (MM/YY).')

            if not re.fullmatch(r'\d{3}', data.get('cvv', '')):
                self.add_error('cvv', 'Enter the 3-digit CVV.')

        elif method == 'NETBANKING':
            if not data.get('bank'):
                self.add_error('bank', 'Please select your bank.')

        return data