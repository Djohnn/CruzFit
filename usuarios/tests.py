from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import LoginAttempt
from .services import (
    LOCKOUT_DURATION,
    LOGIN_FAILURE_LIMIT,
    LOGIN_FAILURE_WINDOW,
    _attempt_filter,
    _is_recoverable_sqlite_lock,
    registrar_falha_login,
)

User = get_user_model()


class LoginRateLimitTests(TestCase):
    def setUp(self):
        User.objects.create_user(
            username='owner@example.test',
            first_name='Owner',
            email='owner@example.test',
            password='secret-pass',
            cargo='G',
        )

    def post_login(self, identifier, password='wrong-pass', *, ip='127.0.0.1', forwarded=''):
        return Client().post(
            reverse('login'),
            {'identificador': identifier, 'senha': password},
            REMOTE_ADDR=ip,
            HTTP_X_FORWARDED_FOR=forwarded,
        )

    def test_success_clears_prior_failures_for_any_supported_identifier(self):
        self.post_login('Owner')
        response = self.post_login('owner@example.test', 'secret-pass')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('home'))
        self.assertFalse(LoginAttempt.objects.exists())

    def test_lockout_expires_at_the_configured_instant(self):
        now = timezone.now()
        with patch('usuarios.services.timezone.now', return_value=now):
            for _ in range(LOGIN_FAILURE_LIMIT):
                self.post_login('Owner')
            locked_response = self.post_login('owner@example.test', 'secret-pass')

        attempt = LoginAttempt.objects.get()
        self.assertEqual(locked_response.status_code, 302)
        self.assertEqual(attempt.locked_until, now + LOCKOUT_DURATION)

        with patch('usuarios.services.timezone.now', return_value=now + LOCKOUT_DURATION):
            expired_response = self.post_login('owner@example.test', 'secret-pass')

        self.assertEqual(expired_response.status_code, 302)
        self.assertEqual(expired_response['Location'], reverse('home'))
        self.assertFalse(LoginAttempt.objects.exists())

    def test_wrong_attempt_after_lock_expiry_starts_a_fresh_window(self):
        request = RequestFactory().post(reverse('login'), REMOTE_ADDR='127.0.0.1')
        now = timezone.now()
        identifier_key, source_key = _attempt_filter(request, 'Owner').values()
        attempt = LoginAttempt.objects.create(
            identifier_key=identifier_key,
            source_key=source_key,
            failures=LOGIN_FAILURE_LIMIT,
            locked_until=now + LOCKOUT_DURATION,
        )
        LoginAttempt.objects.filter(pk=attempt.pk).update(
            updated_at=now + LOCKOUT_DURATION - timedelta(seconds=1)
        )
        with patch('usuarios.services.timezone.now', return_value=now + LOCKOUT_DURATION):
            registrar_falha_login(request, 'Owner')

        attempt.refresh_from_db()
        self.assertEqual(attempt.failures, 1)
        self.assertIsNone(attempt.locked_until)

    def test_lockout_state_survives_new_client_and_database_reload(self):
        for _ in range(LOGIN_FAILURE_LIMIT):
            self.post_login('Owner')
        attempt = LoginAttempt.objects.get()
        key = attempt.identifier_key
        attempt.refresh_from_db()

        response = self.post_login('Owner', 'secret-pass')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LoginAttempt.objects.get().identifier_key, key)

    def test_inactive_user_does_not_authenticate(self):
        user = User.objects.get(username='owner@example.test')
        user.is_active = False
        user.save(update_fields=('is_active',))
        client = Client()

        response = client.post(
            reverse('login'),
            {'identificador': 'Owner', 'senha': 'secret-pass'},
            REMOTE_ADDR='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('login'))
        self.assertEqual(LoginAttempt.objects.get().failures, 1)
        self.assertNotIn('_auth_user_id', client.session)

    def test_unknown_user_gets_same_message_and_separate_bucket(self):
        unknown = self.post_login('does-not-exist')
        known = self.post_login('Owner')
        unknown_messages = [str(message) for message in unknown.wsgi_request._messages]
        known_messages = [str(message) for message in known.wsgi_request._messages]

        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown_messages, known_messages)
        self.assertEqual(LoginAttempt.objects.count(), 2)

    def test_user_and_origin_are_isolated_and_forwarded_header_is_ignored(self):
        User.objects.create_user(
            username='cashier', first_name='Cashier', password='secret-pass', cargo='C'
        )
        self.post_login('Owner', ip='127.0.0.1', forwarded='198.51.100.10')
        self.post_login('Owner', ip='127.0.0.2', forwarded='198.51.100.10')
        self.post_login('Cashier', ip='127.0.0.1', forwarded='198.51.100.10')

        self.assertEqual(LoginAttempt.objects.count(), 3)
        self.assertEqual(LoginAttempt.objects.filter(failures=1).count(), 3)

    def test_stale_identifier_buckets_are_deleted_during_failure_cleanup(self):
        stale = LoginAttempt.objects.create(
            identifier_key='a' * 64,
            source_key='b' * 64,
            failures=1,
        )
        old_time = timezone.now() - LOGIN_FAILURE_WINDOW - LOCKOUT_DURATION
        LoginAttempt.objects.filter(pk=stale.pk).update(updated_at=old_time)
        request = RequestFactory().post(reverse('login'), REMOTE_ADDR='127.0.0.1')

        registrar_falha_login(request, 'Owner')

        self.assertFalse(LoginAttempt.objects.filter(pk=stale.pk).exists())
        self.assertEqual(LoginAttempt.objects.count(), 1)

    def test_failures_below_limit_expire_after_the_configured_window(self):
        request = RequestFactory().post(reverse('login'), REMOTE_ADDR='127.0.0.1')
        now = timezone.now()
        with patch('usuarios.services.timezone.now', return_value=now):
            for _ in range(LOGIN_FAILURE_LIMIT - 1):
                registrar_falha_login(request, 'Owner')
        with patch('usuarios.services.timezone.now', return_value=now + LOGIN_FAILURE_WINDOW):
            registrar_falha_login(request, 'Owner')

        attempt = LoginAttempt.objects.get()
        self.assertEqual(attempt.failures, 1)
        self.assertIsNone(attempt.locked_until)

    def test_alternate_identifiers_share_one_account_bucket(self):
        for _ in range(LOGIN_FAILURE_LIMIT - 1):
            self.post_login('Owner')

        response = self.post_login('owner@example.test')

        self.assertEqual(LoginAttempt.objects.count(), 1)
        self.assertIsNotNone(LoginAttempt.objects.get().locked_until)
        self.assertEqual(response['Location'], reverse('login'))

    def test_unicode_name_and_username_resolve_to_the_same_bucket(self):
        User.objects.create_user(
            username='store-employee', first_name='straße', password='secret-pass', cargo='V'
        )

        self.post_login('straße')
        self.post_login('store-employee')

        self.assertEqual(LoginAttempt.objects.count(), 1)
        self.assertEqual(LoginAttempt.objects.get().failures, 2)

    def test_ambiguous_name_uses_generic_login_error(self):
        User.objects.create_user(username='owner2', first_name='Owner', password='secret-pass', cargo='C')

        response = self.post_login('Owner')

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in response.wsgi_request._messages]
        self.assertEqual(messages, ['Não foi possível entrar. Verifique seu identificador e senha.'])

    def test_csrf_rejection_does_not_change_login_attempt_state(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse('login'),
            {'identificador': 'Owner', 'senha': 'wrong-pass'},
            REMOTE_ADDR='127.0.0.1',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoginAttempt.objects.exists())

    def test_non_sqlite_lock_lookalike_is_rethrown_without_retry(self):
        error = OperationalError('database is locked')
        with (
            patch('usuarios.services.connections') as connections_mock,
            patch('usuarios.services._registrar_falha_login_atomically', side_effect=error) as write,
        ):
            connections_mock.__getitem__.return_value.vendor = 'postgresql'
            with self.assertRaises(OperationalError):
                registrar_falha_login(RequestFactory().post(reverse('login')), 'Owner')

        write.assert_called_once()
        self.assertFalse(_is_recoverable_sqlite_lock(error))

    def test_admin_login_uses_same_limit_for_staff_without_bypassing_csrf(self):
        user = User.objects.get(username='owner@example.test')
        user.is_staff = True
        user.save(update_fields=('is_staff',))
        client = Client()
        for _ in range(LOGIN_FAILURE_LIMIT):
            client.post(reverse('admin:login'), {'username': user.username, 'password': 'wrong'})

        blocked = client.post(
            reverse('admin:login'), {'username': user.username, 'password': 'secret-pass'}
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertTrue(LoginAttempt.objects.get().locked_until)

    def test_csrf_rejection_does_not_change_admin_login_attempt_state(self):
        user = User.objects.get(username='owner@example.test')
        user.is_staff = True
        user.save(update_fields=('is_staff',))
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            reverse('admin:login'), {'username': user.username, 'password': 'wrong'}
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoginAttempt.objects.exists())


class LoginRateLimitConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        User.objects.create_user(
            username='owner', first_name='Owner', password='secret-pass', cargo='G'
        )
        Client().post(reverse('login'), {'identificador': 'Owner', 'senha': 'wrong'})

    def _run_concurrent_failures(self, *, ip, count):
        request = RequestFactory().post(reverse('login'), REMOTE_ADDR=ip)
        barrier = Barrier(count)

        def fail_once(_):
            try:
                barrier.wait()
                registrar_falha_login(request, 'Owner')
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=count) as pool:
            list(pool.map(fail_once, range(count)))
        return request, LoginAttempt.objects.get(**_attempt_filter(request, 'Owner'))

    def test_concurrent_failures_create_one_bucket_and_reach_lockout(self):
        request, attempt = self._run_concurrent_failures(ip='127.0.0.2', count=4)

        self.assertEqual(LoginAttempt.objects.filter(**_attempt_filter(request, 'Owner')).count(), 1)
        self.assertEqual(attempt.failures, 4)
        self.assertIsNone(attempt.locked_until)

        registrar_falha_login(request, 'Owner')
        attempt.refresh_from_db()
        self.assertEqual(attempt.failures, LOGIN_FAILURE_LIMIT)
        self.assertIsNotNone(attempt.locked_until)

    def test_concurrent_failures_on_existing_bucket_are_not_lost(self):
        request, attempt = self._run_concurrent_failures(ip='127.0.0.1', count=3)

        self.assertEqual(attempt.failures, 4)
        self.assertIsNone(attempt.locked_until)

        registrar_falha_login(request, 'Owner')
        attempt.refresh_from_db()
        self.assertEqual(attempt.failures, LOGIN_FAILURE_LIMIT)
        self.assertIsNotNone(attempt.locked_until)
