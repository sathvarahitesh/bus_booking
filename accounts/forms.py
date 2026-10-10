from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import User

phone_validator = RegexValidator(r'^[6-9]\d{9}$', 'Enter a valid 10-digit mobile number.')


class BootstrapMixin:
    """Adds Bootstrap classes to every field."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = (
                'form-select' if isinstance(field.widget, forms.Select) else 'form-control')


class LoginForm(BootstrapMixin, AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.is_staff:
            raise forms.ValidationError(
                'Admin accounts cannot log in here. Please use the admin panel.',
                code='admin_account')


class PasswordForm(BootstrapMixin, PasswordChangeForm):
    pass


class RegisterForm(BootstrapMixin, UserCreationForm):
    phone = forms.CharField(max_length=10, validators=[phone_validator], label='Mobile number')

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('first_name', 'last_name', 'username', 'email', 'phone')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = True
        self.fields['email'].required = True

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


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