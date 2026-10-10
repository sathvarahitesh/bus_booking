from datetime import timedelta

from django import forms
from django.utils import timezone

MAX_DAYS_AHEAD = 120


class SearchForm(forms.Form):
    source = forms.CharField(max_length=60)
    destination = forms.CharField(max_length=60)
    date = forms.DateField(required=False)

    def clean(self):
        data = super().clean()
        source = data.get('source')
        destination = data.get('destination')
        if source and destination and source.lower() == destination.lower():
            self.add_error('destination', 'Source and destination cannot be the same.')

        if 'date' not in self.errors:
            today = timezone.localdate()
            travel_date = data.get('date') or today
            if travel_date < today:
                self.add_error('date', 'Travel date cannot be in the past.')
            elif travel_date > today + timedelta(days=MAX_DAYS_AHEAD):
                self.add_error('date', f'Bookings open only {MAX_DAYS_AHEAD} days ahead.')
            else:
                data['date'] = travel_date
        return data