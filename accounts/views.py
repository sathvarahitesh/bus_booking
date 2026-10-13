import time

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme

from core.throttle import MESSAGE as THROTTLE_MESSAGE
from core.throttle import limit_reached, throttle_post

from .forms import EmailLoginForm, OTPForm, PasswordForm, ProfileForm, RegisterForm
from .models import User
from .otp import cooldown_remaining, mask_email, send_otp, verify_code

PENDING_KEY = 'otp_pending'
PENDING_TTL = 30 * 60  # seconds a half-finished OTP step stays valid


def safe_next(request, url):
    if url and url_has_allowed_host_and_scheme(
            url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return ''


def start_otp_flow(request, user, purpose, next_url=''):
    """Remember who is waiting for an OTP. The user is NOT logged in yet."""
    request.session[PENDING_KEY] = {
        'user_id': user.pk, 'purpose': purpose, 'next': next_url, 'started': time.time()}


def get_pending(request):
    """Return (user, purpose) of the OTP step in progress, or (None, None)."""
    data = request.session.get(PENDING_KEY)
    if not data or time.time() - data.get('started', 0) > PENDING_TTL:
        request.session.pop(PENDING_KEY, None)
        return None, None
    user = User.objects.filter(pk=data['user_id']).first()
    purpose = data['purpose']
    valid = user is not None and (
        (purpose == 'REGISTER' and not user.email_verified)
        or (purpose == 'LOGIN' and user.is_active and not user.is_staff))
    if not valid:
        request.session.pop(PENDING_KEY, None)
        return None, None
    return user, purpose


def complete_otp_flow(request, user, purpose):
    data = request.session.pop(PENDING_KEY, {})
    if purpose == 'REGISTER':
        user.is_active = True
        user.email_verified = True
        user.save(update_fields=['is_active', 'email_verified'])
        messages.success(request, 'Email verified. Your account is ready.')
    else:
        messages.success(request, f'Welcome back, {user.first_name or user.get_username()}!')
    login(request, user)
    return redirect(safe_next(request, data.get('next', '')) or 'core:home')


@throttle_post('login', limit=10)
def login_view(request):
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('core:home')

    if request.method == 'POST':
        email_key = request.POST.get('email', '').strip().lower()
        if email_key and limit_reached(request, 'login-email', limit=10, window=900, ident=email_key):
            messages.error(request, THROTTLE_MESSAGE)
            return redirect('accounts:login')

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    form = EmailLoginForm(request, request.POST or None)

    if request.method == 'POST' and form.is_valid():
        if form.unverified:
            user, purpose = form.unverified, 'REGISTER'
            messages.info(request, 'Please verify your email first. We sent you a new code.')
        else:
            user, purpose = form.user, 'LOGIN'
        ok, message = send_otp(user, purpose)
        start_otp_flow(request, user, purpose, safe_next(request, next_url))
        if ok:
            messages.success(request, f'We sent a 6-digit code to {mask_email(user.email)}.')
        else:
            messages.warning(request, message)
        return redirect('accounts:verify')

    return render(request, 'accounts/login.html', {'form': form, 'next': next_url})


@throttle_post('register', limit=10)
def register(request):
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('core:home')

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    form = RegisterForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        user = form.save_pending()
        ok, message = send_otp(user, 'REGISTER')
        start_otp_flow(request, user, 'REGISTER', safe_next(request, next_url))
        if ok:
            messages.success(request, f'We sent a 6-digit code to {mask_email(user.email)}.')
        else:
            messages.warning(request, message)
        return redirect('accounts:verify')

    return render(request, 'accounts/register.html', {'form': form, 'next': next_url})


@throttle_post('otp', limit=30)
def verify(request):
    user, purpose = get_pending(request)
    if user is None:
        messages.error(request, 'Your verification session has expired. Please start again.')
        return redirect('accounts:register')

    form = OTPForm(request.POST or None)
    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            ok, message = send_otp(user, purpose)
            if ok:
                messages.success(request, f'A new code was sent to {mask_email(user.email)}.')
            else:
                messages.warning(request, message)
            return redirect('accounts:verify')
        if form.is_valid():
            ok, message = verify_code(user, purpose, form.cleaned_data['code'])
            if ok:
                return complete_otp_flow(request, user, purpose)
            form.add_error('code', message)

    return render(request, 'accounts/verify_otp.html', {
        'form': form,
        'email': mask_email(user.email),
        'cooldown': cooldown_remaining(user, purpose),
        'back_url': reverse('accounts:register' if purpose == 'REGISTER' else 'accounts:login'),
    })


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('accounts:profile')
    return render(request, 'accounts/profile.html', {'form': form})


class UserPasswordChangeView(SuccessMessageMixin, PasswordChangeView):
    form_class = PasswordForm
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('accounts:profile')
    success_message = 'Password changed successfully.'