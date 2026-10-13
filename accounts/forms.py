import secrets
from django.contrib.auth import authenticate
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import User

phone_validator = RegexValidator(r'^[6-9]\d{9}$', 'Enter a valid 10-digit mobile number.')
name_validator = RegexValidator(
    r"^[A-Za-z][A-Za-z .'-]*$", 'Enter a valid name (letters and spaces only).')


class BootstrapMixin:
    """Adds Bootstrap classes to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = (
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control')


class EmailLoginForm(BootstrapMixin, forms.Form):
    """Step 1 of login: email + password. The OTP step follows in the view."""
    email = forms.EmailField(
        max_length=150,
        widget=forms.EmailInput(attrs={'autocomplete': 'email', 'autofocus': True}))
    password = forms.CharField(
        strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))

    INVALID = 'Please enter a correct email and password.'
    ADMIN = 'Admin accounts cannot log in here. Please use the admin panel.'

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None        # verified customer, ready for the OTP step
        self.unverified = None  # correct password but the email was never verified
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        email, password = data.get('email'), data.get('password')
        if not (email and password):
            return data

        candidate = User.objects.filter(email__iexact=email.strip()).first()
        if candidate is None:
            User().set_password(password)  # same hashing time, so timing does not reveal the email
            raise forms.ValidationError(self.INVALID, code='invalid')

        user = authenticate(self.request, username=candidate.username, password=password)
        if user is not None:
            if user.is_staff:
                raise forms.ValidationError(self.ADMIN, code='admin_account')
            self.user = user
        elif not candidate.email_verified and candidate.check_password(password):
            self.unverified = candidate
        else:
            raise forms.ValidationError(self.INVALID, code='invalid')
        return data


class PasswordForm(BootstrapMixin, PasswordChangeForm):
    pass


class RegisterForm(BootstrapMixin, forms.Form):
    name = forms.CharField(max_length=100, label='Full name', validators=[name_validator])
    email = forms.EmailField(max_length=150)
    phone = forms.CharField(max_length=10, validators=[phone_validator], label='Mobile number')
    password1 = forms.CharField(
        label='Password', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='At least 8 characters. Not too common and not only digits.')
    password2 = forms.CharField(
        label='Confirm password', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_user = None  # an earlier, never-verified registration with this email

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        existing = User.objects.filter(email__iexact=email).first()
        if existing and existing.email_verified:
            raise forms.ValidationError('An account with this email already exists. Please login.')
        self.pending_user = existing
        return email

    def clean(self):
        data = super().clean()
        password1, password2 = data.get('password1'), data.get('password2')
        if password1 and password2:
            if password1 != password2:
                self.add_error('password2', 'The two passwords do not match.')
            else:
                try:
                    validate_password(password1, user=User(
                        email=data.get('email', ''), first_name=data.get('name', '')))
                except forms.ValidationError as error:
                    self.add_error('password1', error)
        return data

    def save_pending(self):
        """Create (or refresh) an inactive account that waits for email verification."""
        data = self.cleaned_data
        first_name, _, last_name = data['name'].strip().partition(' ')
        user = self.pending_user
        if user is None:
            username = data['email']
            if User.objects.filter(username__iexact=username).exists():
                username = f"{data['email'].split('@')[0][:120]}-{secrets.token_hex(3)}"
            user = User(username=username)
        user.email = data['email']
        user.first_name = first_name
        user.last_name = last_name.strip()
        user.phone = data['phone']
        user.is_active = False
        user.email_verified = False
        user.set_password(data['password1'])
        user.save()
        return user


class OTPForm(BootstrapMixin, forms.Form):
    code = forms.CharField(
        min_length=6, max_length=6, label='6-digit code',
        widget=forms.TextInput(attrs={
            'inputmode': 'numeric', 'autocomplete': 'one-time-code',
            'placeholder': '000000'}))

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if not code.isdigit():
            raise forms.ValidationError('Enter the 6 digits from the email.')
        return code


class ProfileForm(BootstrapMixin, forms.ModelForm):
    phone = forms.CharField(max_length=10, required=False, validators=[phone_validator],
                            label='Mobile number')

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'gender', 'date_of_birth')
        widgets = {
            'date_of_birth': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['email'].required = True
        self.fields['email'].disabled = True  # verified by OTP, so it is not editable here

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('This email is used by another account.')
        return email

    def clean_date_of_birth(self):
        dob = self.cleaned_data.get('date_of_birth')
        if dob and dob > timezone.localdate():
            raise forms.ValidationError('Date of birth cannot be in the future.')
        return dob