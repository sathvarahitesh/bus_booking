import re
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import EmailOTP, User
from .otp import MAX_ATTEMPTS, send_otp, verify_code


def code_in_last_mail():
    return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)


class OTPServiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            'o@example.com', 'o@example.com', 'Str0ng#Pass99', first_name='Omi')

    def age_codes(self):
        EmailOTP.objects.update(created_at=timezone.now() - timedelta(minutes=2))

    def test_code_is_emailed_and_stored_hashed(self):
        ok, _ = send_otp(self.user, 'LOGIN')
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['o@example.com'])
        otp = EmailOTP.objects.get()
        self.assertEqual(len(otp.code_hash), 64)
        self.assertNotIn(code_in_last_mail(), otp.code_hash)

    def test_code_works_only_once(self):
        send_otp(self.user, 'LOGIN')
        code = code_in_last_mail()
        self.assertTrue(verify_code(self.user, 'LOGIN', code)[0])
        self.assertFalse(verify_code(self.user, 'LOGIN', code)[0])

    def test_wrong_attempts_lock_the_code(self):
        send_otp(self.user, 'LOGIN')
        code = code_in_last_mail()
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(MAX_ATTEMPTS):
            self.assertFalse(verify_code(self.user, 'LOGIN', wrong)[0])
        self.assertFalse(verify_code(self.user, 'LOGIN', code)[0])

    def test_expired_code_is_rejected(self):
        send_otp(self.user, 'LOGIN')
        code = code_in_last_mail()
        EmailOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        ok, message = verify_code(self.user, 'LOGIN', code)
        self.assertFalse(ok)
        self.assertIn('expired', message)

    def test_resend_cooldown_and_hourly_limit(self):
        self.assertTrue(send_otp(self.user, 'LOGIN')[0])
        ok, message = send_otp(self.user, 'LOGIN')
        self.assertFalse(ok)
        self.assertIn('wait', message)
        self.assertEqual(len(mail.outbox), 1)

        for _ in range(4):
            self.age_codes()
            self.assertTrue(send_otp(self.user, 'LOGIN')[0])
        self.age_codes()
        ok, message = send_otp(self.user, 'LOGIN')
        self.assertFalse(ok)
        self.assertIn('Too many', message)

    def test_new_code_replaces_the_old_one(self):
        send_otp(self.user, 'LOGIN')
        first = code_in_last_mail()
        self.age_codes()
        send_otp(self.user, 'LOGIN')
        second = code_in_last_mail()
        if first != second:
            self.assertFalse(verify_code(self.user, 'LOGIN', first)[0])
        self.assertTrue(verify_code(self.user, 'LOGIN', second)[0])


class RegistrationTests(TestCase):
    password = 'Str0ng#Pass99'

    def setUp(self):
        cache.clear()
        self.data = {
            'name': 'Asha Patel', 'email': 'Asha@Example.com', 'phone': '9876543210',
            'password1': self.password, 'password2': self.password,
        }

    def register(self, **changes):
        return self.client.post(reverse('accounts:register'), {**self.data, **changes})

    def test_registration_creates_inactive_user_and_sends_code(self):
        response = self.register()
        self.assertRedirects(response, reverse('accounts:verify'))
        user = User.objects.get(email='asha@example.com')
        self.assertFalse(user.is_active)
        self.assertFalse(user.email_verified)
        self.assertEqual(user.username, 'asha@example.com')
        self.assertEqual((user.first_name, user.last_name), ('Asha', 'Patel'))
        self.assertEqual(len(mail.outbox), 1)

    def test_cannot_login_before_verification(self):
        self.register()
        self.assertFalse(self.client.login(username='asha@example.com', password=self.password))

    def test_correct_code_activates_and_logs_in(self):
        self.register()
        response = self.client.post(reverse('accounts:verify'), {'code': code_in_last_mail()})
        self.assertRedirects(response, reverse('core:home'))
        user = User.objects.get(email='asha@example.com')
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified)
        self.assertIn('_auth_user_id', self.client.session)

    def test_wrong_code_does_not_activate(self):
        self.register()
        code = code_in_last_mail()
        wrong = '000000' if code != '000000' else '111111'
        response = self.client.post(reverse('accounts:verify'), {'code': wrong})
        self.assertContains(response, 'Incorrect code')
        self.assertFalse(User.objects.get(email='asha@example.com').is_active)

    def test_verified_email_cannot_register_again(self):
        User.objects.create_user('asha@example.com', 'asha@example.com', self.password)
        response = self.register()
        self.assertContains(response, 'already exists')
        self.assertEqual(len(mail.outbox), 0)

    def test_unverified_registration_can_be_restarted(self):
        self.register()
        self.register(name='Asha Shah')
        self.assertEqual(User.objects.filter(email='asha@example.com').count(), 1)
        self.assertEqual(User.objects.get(email='asha@example.com').last_name, 'Shah')
        self.assertEqual(len(mail.outbox), 1)  # the 60 second cooldown blocked a second email

    def test_verify_page_needs_a_started_registration(self):
        response = self.client.get(reverse('accounts:verify'))
        self.assertRedirects(response, reverse('accounts:register'))

    def test_password_mismatch_is_rejected(self):
        response = self.register(password2='Different#Pass1')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='asha@example.com').exists())

    def test_invalid_phone_is_rejected(self):
        response = self.register(phone='12345')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='asha@example.com').exists())

    def test_resend_sends_a_new_code(self):
        self.register()
        EmailOTP.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        response = self.client.post(reverse('accounts:verify'), {'action': 'resend'})
        self.assertRedirects(response, reverse('accounts:verify'))
        self.assertEqual(len(mail.outbox), 2)


class LoginOTPTests(TestCase):
    password = 'Str0ng#Pass99'

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            'lina', 'Lina@Example.com', self.password, first_name='Lina')

    def login(self, email='lina@example.com', password=None, **extra):
        return self.client.post(reverse('accounts:login'), {
            'email': email, 'password': password or self.password, **extra})

    def test_password_alone_does_not_log_in(self):
        response = self.login()
        self.assertRedirects(response, reverse('accounts:verify'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_email_is_case_insensitive(self):
        response = self.login(email='LINA@EXAMPLE.COM')
        self.assertRedirects(response, reverse('accounts:verify'))

    def test_correct_code_completes_login(self):
        self.login()
        response = self.client.post(reverse('accounts:verify'), {'code': code_in_last_mail()})
        self.assertRedirects(response, reverse('core:home'))
        self.assertEqual(self.client.session['_auth_user_id'], str(self.user.pk))

    def test_wrong_password_sends_no_code(self):
        response = self.login(password='Wrong#Pass123')
        self.assertContains(response, 'correct email and password')
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_email_gets_the_same_error(self):
        response = self.login(email='nobody@example.com')
        self.assertContains(response, 'correct email and password')
        self.assertEqual(len(mail.outbox), 0)

    def test_used_code_cannot_be_replayed(self):
        self.login()
        code = code_in_last_mail()
        self.client.post(reverse('accounts:verify'), {'code': code})
        self.client.post(reverse('accounts:logout'))
        self.login()  # new password step; the cooldown blocks a second email
        response = self.client.post(reverse('accounts:verify'), {'code': code})
        self.assertContains(response, 'No active code')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_account_is_refused(self):
        User.objects.create_user('boss', 'boss@example.com', self.password, is_staff=True)
        response = self.login(email='boss@example.com')
        self.assertContains(response, 'Admin accounts cannot log in here')
        self.assertEqual(len(mail.outbox), 0)

    def test_deactivated_account_cannot_login(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        response = self.login()
        self.assertContains(response, 'correct email and password')
        self.assertEqual(len(mail.outbox), 0)

    def test_unverified_account_is_sent_to_verification(self):
        pending = User.objects.create_user(
            'pending@example.com', 'pending@example.com', self.password,
            first_name='Pam', is_active=False, email_verified=False)
        response = self.login(email='pending@example.com')
        self.assertRedirects(response, reverse('accounts:verify'))
        self.client.post(reverse('accounts:verify'), {'code': code_in_last_mail()})
        pending.refresh_from_db()
        self.assertTrue(pending.is_active)
        self.assertTrue(pending.email_verified)

    def test_next_page_is_kept_after_otp(self):
        self.login(next='/bookings/my/')
        response = self.client.post(reverse('accounts:verify'), {'code': code_in_last_mail()})
        self.assertRedirects(response, '/bookings/my/')

    def test_too_many_attempts_are_blocked(self):
        for _ in range(10):
            self.login(password='Wrong#Pass123')
        response = self.login()  # even the correct password is blocked now
        self.assertRedirects(response, reverse('accounts:login'))
        self.assertEqual(len(mail.outbox), 0)