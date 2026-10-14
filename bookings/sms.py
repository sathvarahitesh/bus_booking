import json
from base64 import b64encode
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

TIMEOUT_SECONDS = 10


def provider_name():
    return (getattr(settings, 'SMS_PROVIDER', '') or '').strip().lower()


def to_e164(phone):
    """10-digit Indian mobile numbers get the default country code (SMS_COUNTRY_CODE)."""
    digits = ''.join(ch for ch in phone if ch.isdigit())
    if phone.strip().startswith('+'):
        return '+' + digits
    return f"{settings.SMS_COUNTRY_CODE}{digits.lstrip('0')}"


def send_sms(phone, text):
    """Send one SMS. Returns (status, detail).

    UNAVAILABLE = no provider configured, SIMULATED = development printout only,
    SENT = the provider accepted it (this does not prove the handset received it), FAILED = error.
    """
    provider = provider_name()
    if not provider:
        return 'UNAVAILABLE', 'No SMS provider is configured (set SMS_PROVIDER).'
    if provider == 'console':
        print(f'[SMS simulation] To {phone}: {text}')
        return 'SIMULATED', 'Printed in the server console. Not delivered to a phone.'
    if provider == 'twilio':
        return _send_twilio(phone, text)
    return 'UNAVAILABLE', f'Unknown SMS provider "{provider}".'


def _twilio_message(error):
    try:
        return json.loads(error.read().decode()).get('message', str(error.reason))
    except Exception:
        return str(error.reason)


def _send_twilio(phone, text):
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    sender = settings.TWILIO_FROM_NUMBER
    if not (sid and token and sender):
        return 'UNAVAILABLE', 'Twilio is selected but the account SID, auth token or from number is missing.'

    url = f"{settings.TWILIO_API_BASE.rstrip('/')}/2010-04-01/Accounts/{sid}/Messages.json"
    body = urlencode({'To': to_e164(phone), 'From': sender, 'Body': text}).encode()
    request = Request(url, data=body, method='POST')
    request.add_header('Authorization', 'Basic ' + b64encode(f'{sid}:{token}'.encode()).decode())
    request.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode() or '{}'
    except HTTPError as error:
        return 'FAILED', f'Twilio rejected the request ({error.code}): {_twilio_message(error)}'
    except (URLError, TimeoutError, OSError) as error:
        return 'FAILED', f'Could not reach Twilio: {error}'
    try:
        status = json.loads(raw).get('status', 'queued')
    except ValueError:
        status = 'queued'
    return 'SENT', f'Accepted by Twilio (status: {status}). Delivery to the handset is not confirmed here.'