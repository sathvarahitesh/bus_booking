from django import forms
from django.core.validators import RegexValidator

from accounts.forms import BootstrapMixin, phone_validator

from .models import Passenger

name_validator = RegexValidator(
    r"^[A-Za-z][A-Za-z .'-]*$", 'Enter a valid name (letters and spaces only).')


class PassengerForm(BootstrapMixin, forms.ModelForm):
    name = forms.CharField(max_length=100, label='Full name', validators=[name_validator])

    class Meta:
        model = Passenger
        fields = ('name', 'age', 'gender')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['gender'].choices = [('', 'Select gender'), ('M', 'Male'), ('F', 'Female')]


class ContactForm(BootstrapMixin, forms.Form):
    phone = forms.CharField(max_length=10, validators=[phone_validator], label='Mobile number')
    email = forms.EmailField(required=False, label='Email (optional)')


class FindBookingForm(BootstrapMixin, forms.Form):
    pnr = forms.CharField(max_length=12, label='PNR')
    phone = forms.CharField(max_length=10, validators=[phone_validator], label='Mobile number')

    def clean_pnr(self):
        return self.cleaned_data['pnr'].strip().upper()