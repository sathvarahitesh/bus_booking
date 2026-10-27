import time

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from core.throttle import MESSAGE as THROTTLE_MESSAGE
from core.throttle import limit_reached, throttle_post

from .forms import (EmailLoginForm, ForgotPasswordForm, NewPasswordForm, OTPForm,
                    PasswordForm, ProfileForm, RegisterForm)
from .models import EmailOTP, User
from .otp import (RESEND_COOLDOWN_SECONDS, cooldown_remaining, mask_email, send_otp,
                  send_password_changed_notice, verify_code)

PENDING_KEY = 'otp_pending'
PENDING_TTL = 30 * 60  # seconds a half-finished OTP step stays valid

RESET_KEY = 'reset_pending'      # step 1 done: a code was requested for this email
RESET_OK_KEY = 'reset_verified'  # step 2 done: the code was correct
RESET_TTL = 30 * 60
RESET_VERIFIED_TTL = 10 * 60


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


# ---------- Forgot password: email -> 6-digit code -> new password ----------

def reset_candidate(user_id):
    """The customer account that may reset its password here, otherwise None."""
    if not user_id:
        return None
    return User.objects.filter(
        pk=user_id, is_active=True, email_verified=True, is_staff=False).first()


@throttle_post('forgot', limit=10)
def forgot_password(request):
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('accounts:password_change')  # logged-in users change it from their profile

    form = ForgotPasswordForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        if limit_reached(request, 'forgot-email', limit=5, window=3600, ident=email):
            messages.error(request, THROTTLE_MESSAGE)
            return redirect('accounts:forgot_password')

        # The same page is shown whether or not the email belongs to an account.
        user = reset_candidate(
            User.objects.filter(email__iexact=email).values_list('pk', flat=True).first())
        if user:
            send_otp(user, 'RESET')
        request.session[RESET_KEY] = {
            'email': email, 'user_id': user.pk if user else None, 'started': time.time()}
        messages.info(request, 'If an account exists for that email, we sent a 6-digit code. '
                               'It expires in 5 minutes.')
        return redirect('accounts:reset_verify')

    return render(request, 'accounts/forgot_password.html', {'form': form})


@throttle_post('reset-otp', limit=30)
def reset_verify(request):
    pending = request.session.get(RESET_KEY)
    if not pending or time.time() - pending.get('started', 0) > RESET_TTL:
        request.session.pop(RESET_KEY, None)
        messages.error(request, 'Your reset session expired. Please start again.')
        return redirect('accounts:forgot_password')

    user = reset_candidate(pending.get('user_id'))
    form = OTPForm(request.POST or None)

    if request.method == 'POST':
        if request.POST.get('action') == 'resend':
            if user:
                ok, message = send_otp(user, 'RESET')
                if not ok:
                    messages.warning(request, message)
                    return redirect('accounts:reset_verify')
            messages.info(request, 'If an account exists for that email, a new code was sent.')
            return redirect('accounts:reset_verify')
        if form.is_valid():
            if user:
                ok, message = verify_code(user, 'RESET', form.cleaned_data['code'])
            else:
                ok, message = False, 'Incorrect code. Please check it or request a new one.'
            if ok:
                request.session.pop(RESET_KEY, None)
                request.session[RESET_OK_KEY] = {'user_id': user.pk, 'at': time.time()}
                return redirect('accounts:reset_password')
            form.add_error('code', message)

    if user:
        cooldown = cooldown_remaining(user, 'RESET')
    else:  # unknown email: show the same countdown so the page looks identical
        cooldown = max(0, RESEND_COOLDOWN_SECONDS - int(time.time() - pending['started']))
    return render(request, 'accounts/verify_otp.html', {
        'form': form,
        'email': mask_email(pending['email']),
        'cooldown': cooldown,
        'back_url': reverse('accounts:forgot_password'),
    })


@throttle_post('reset-password', limit=20)
def reset_password(request):
    data = request.session.get(RESET_OK_KEY)
    if not data or time.time() - data.get('at', 0) > RESET_VERIFIED_TTL:
        request.session.pop(RESET_OK_KEY, None)
        messages.error(request, 'Please verify the code from your email first.')
        return redirect('accounts:forgot_password')
    user = reset_candidate(data.get('user_id'))
    if user is None:
        request.session.pop(RESET_OK_KEY, None)
        return redirect('accounts:forgot_password')

    form = NewPasswordForm(user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()  # sets the new password; the user's other sessions stop working
        request.session.pop(RESET_OK_KEY, None)
        EmailOTP.objects.filter(user=user, purpose='RESET', used_at__isnull=True).update(
            used_at=timezone.now())
        send_password_changed_notice(user)
        messages.success(request, 'Your password was changed. Please log in with your new password.')
        return redirect('accounts:login')

    return render(request, 'accounts/reset_password.html', {'form': form})


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