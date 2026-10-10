from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from core.throttle import ThrottledPostMixin

from .forms import LoginForm, PasswordForm, ProfileForm, RegisterForm


class UserLoginView(ThrottledPostMixin, LoginView):
    throttle_scope = 'login'
    template_name = 'accounts/login.html'
    authentication_form = LoginForm

    def form_valid(self, form):
        messages.success(self.request, f'Welcome back, {form.get_user().get_username()}!')
        return super().form_valid(form)


def register(request):
    if request.user.is_authenticated and not request.user.is_staff:
        return redirect('core:home')

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    form = RegisterForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Account created successfully. Welcome aboard!')
        if next_url and url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            return redirect(next_url)
        return redirect('core:home')

    return render(request, 'accounts/register.html', {'form': form, 'next': next_url})


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