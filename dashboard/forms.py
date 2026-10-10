from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.forms import modelform_factory

from django.utils import timezone

from buses.models import Bus, Route

from accounts.forms import BootstrapMixin


class StaffLoginForm(BootstrapMixin, AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise forms.ValidationError('This area is for administrators only.', code='not_staff')


def make_form_class(model, fields):
    """Build a Bootstrap-styled ModelForm for any model."""
    base = modelform_factory(model, fields=fields)

    class StyledForm(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for field in self.fields.values():
                widget = field.widget
                if isinstance(widget, forms.CheckboxInput):
                    css = 'form-check-input'
                elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                    css = 'form-select'
                else:
                    css = 'form-control'
                widget.attrs['class'] = css
                if isinstance(widget, forms.DateInput):
                    widget.attrs['type'] = 'date'
                elif isinstance(widget, forms.TimeInput):
                    widget.attrs['type'] = 'time'

    return StyledForm


WEEKDAYS = [(0, 'Mon'), (1, 'Tue'), (2, 'Wed'), (3, 'Thu'), (4, 'Fri'), (5, 'Sat'), (6, 'Sun')]
MAX_RANGE_DAYS = 120


class BulkScheduleForm(BootstrapMixin, forms.Form):
    bus = forms.ModelChoiceField(
        queryset=Bus.objects.filter(is_active=True).select_related('operator'))
    route = forms.ModelChoiceField(queryset=Route.objects.all())
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    departure_time = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}))
    weekdays = forms.TypedMultipleChoiceField(
        choices=WEEKDAYS, coerce=int, initial=[0, 1, 2, 3, 4, 5, 6],
        widget=forms.CheckboxSelectMultiple, label='Run on')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['weekdays'].widget.attrs['class'] = 'form-check-input'

    def clean(self):
        data = super().clean()
        start, end = data.get('start_date'), data.get('end_date')
        if start and end:
            if start < timezone.localdate():
                self.add_error('start_date', 'Start date cannot be in the past.')
            if end < start:
                self.add_error('end_date', 'End date must be on or after the start date.')
            elif (end - start).days > MAX_RANGE_DAYS:
                self.add_error('end_date', f'Maximum range is {MAX_RANGE_DAYS} days.')
        return data