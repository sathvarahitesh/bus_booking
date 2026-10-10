from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .config import ENTITIES


class DashboardAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user('cust', 'c@example.com', 'pass12345')
        User.objects.create_user('boss', 'b@example.com', 'pass12345', is_staff=True)

    def test_anonymous_is_sent_to_dashboard_login(self):
        response = self.client.get(reverse('dashboard:index'))
        self.assertRedirects(response, f"{reverse('dashboard:login')}?next={reverse('dashboard:index')}")

    def test_customer_cannot_open_dashboard(self):
        self.client.login(username='cust', password='pass12345')
        response = self.client.get(reverse('dashboard:index'))
        self.assertEqual(response.status_code, 302)

    def test_customer_cannot_use_admin_login(self):
        response = self.client.post(reverse('dashboard:login'), {'username': 'cust', 'password': 'pass12345'})
        self.assertContains(response, 'administrators only')

    def test_staff_can_open_every_page(self):
        self.client.login(username='boss', password='pass12345')
        names = ['dashboard:index', 'dashboard:reports', 'dashboard:bulk_schedules']
        for name in names:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for key, cfg in ENTITIES.items():
            self.assertEqual(self.client.get(reverse('dashboard:list', args=[key])).status_code, 200, key)
            if cfg['create']:
                self.assertEqual(self.client.get(reverse('dashboard:add', args=[key])).status_code, 200, key)