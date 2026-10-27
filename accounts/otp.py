import hashlib
import hmac
import logging
import math
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailOTP

logger = logging.getLogger(__name__)

OTP_LENGTH = 6
OTP_LIFETIME_MINUTES = 5
RESEND_COOLDOWN_SECONDS = 60
MAX_ATTEMPTS = 5
MAX_SENDS_PER_HOUR = 5

ACTION_TEXT = {
    'REGISTER': 'complete your registration',
    'LOGIN': 'log in to your account',
    'RESET': 'reset your password',
}


def mask_email(email):
    name, _, domain = email.partition('@')
    shown = name[:2] if len(name) > 2 else name[:1]
    return f"{shown}{'*' * max(len(name) - len(shown), 3)}@{domain}"


def _hash(user_id, purpose, code):
    message = f'{user_id}:{purpose}:{code}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def cooldown_remaining(user, purpose):
    """Seconds the user must still wait before a new code can be sent."""
    last = EmailOTP.objects.filter(user=user, purpose=purpose).order_by('-created_at').first()
    if last is None:
        return 0
    wait = RESEND_COOLDOWN_SECONDS - (timezone.now() - last.created_at).total_seconds()
    return math.ceil(wait) if wait > 0 else 0


def _email_code(user, purpose, code):
    context = {
        'name': user.first_name or 'there',
        'code': code,
        'minutes': OTP_LIFETIME_MINUTES,
        'action': ACTION_TEXT[purpose],
        'site_name': 'YatraBus',
    }
    message = EmailMultiAlternatives(
        subject='Your YatraBus verification code',
        body=render_to_string('emails/otp.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(render_to_string('emails/otp.html', context), 'text/html')
    message.send()


def send_otp(user, purpose):
    """Create and email a new code. Returns (ok, message)."""
    wait = cooldown_remaining(user, purpose)
    if wait:
        return False, f'Please wait {wait} seconds before requesting a new code.'
    since = timezone.now() - timedelta(hours=1)
    recent = EmailOTP.objects.filter(user=user, purpose=purpose, created_at__gte=since).count()
    if recent >= MAX_SENDS_PER_HOUR:
        return False, 'Too many codes requested. Please try again in an hour.'

    code = f'{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}'
    now = timezone.now()
    with transaction.atomic():
        # Older unused codes stop working as soon as a new one is issued
        EmailOTP.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
        otp = EmailOTP.objects.create(
            user=user, purpose=purpose, code_hash=_hash(user.pk, purpose, code),
            expires_at=now + timedelta(minutes=OTP_LIFETIME_MINUTES))
    try:
        _email_code(user, purpose, code)
    except Exception:
        logger.exception('Could not send OTP email to user %s', user.pk)
        otp.delete()
        return False, 'We could not send the email right now. Please try again in a minute.'
    return True, ''


def verify_code(user, purpose, code):
    """Check a code. Single use, limited attempts. Returns (ok, message)."""
    code = (code or '').strip()
    now = timezone.now()
    with transaction.atomic():
        otp = (EmailOTP.objects.select_for_update()
               .filter(user=user, purpose=purpose, used_at__isnull=True)
               .order_by('-created_at').first())
        if otp is None:
            return False, 'No active code. Please request a new one.'
        if otp.expires_at <= now:
            return False, 'This code has expired. Please request a new one.'
        if otp.attempts >= MAX_ATTEMPTS:
            return False, 'Too many wrong attempts. Please request a new code.'

        otp.attempts += 1
        if not hmac.compare_digest(otp.code_hash, _hash(user.pk, purpose, code)):
            otp.save(update_fields=['attempts'])
            left = MAX_ATTEMPTS - otp.attempts
            if left <= 0:
                return False, 'Too many wrong attempts. Please request a new code.'
            return False, f"Incorrect code. {left} attempt{'s' if left != 1 else ''} left."

        otp.used_at = now
        otp.save(update_fields=['attempts', 'used_at'])
    return True, ''


def send_password_changed_notice(user):
    """Tell the owner that the password was changed. A failure is logged and never raised."""
    try:
        message = EmailMultiAlternatives(
            subject='Your YatraBus password was changed',
            body=render_to_string('emails/password_changed.txt', {
                'name': user.first_name or 'there', 'site_name': 'YatraBus'}),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        message.send()
    except Exception:
        logger.exception('Could not send the password-changed notice to user %s', user.pk)