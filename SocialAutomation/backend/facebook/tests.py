import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from .models import FacebookAccount, FacebookPage
from .oauth import (
    FacebookIntegrationError,
    FacebookPermissionMissing,
    FacebookTokenExpired,
    OAUTH_STATE_CREATED_SESSION_KEY,
    OAUTH_STATE_SESSION_KEY,
    exchange_authorization_code,
    get_authorized_pages,
    validate_oauth_state,
)
from .services import refresh_authorized_pages, save_authorized_pages
from .tokens import TokenEncryptionError, decrypt_token, encrypt_token


TEST_FERNET_KEY = Fernet.generate_key().decode('ascii')


@override_settings(
    FACEBOOK_APP_ID='test-facebook-app-id',
    FACEBOOK_APP_SECRET='test-facebook-app-secret',
    FACEBOOK_REDIRECT_URI='http://localhost:8000/api/facebook/oauth/callback/',
    FACEBOOK_FRONTEND_URL='http://localhost:5173',
    FACEBOOK_GRAPH_API_VERSION='v27.0',
    FACEBOOK_TOKEN_ENCRYPTION_KEY=TEST_FERNET_KEY,
    FACEBOOK_LOGIN_SCOPES=('pages_show_list', 'pages_read_engagement', 'pages_manage_posts'),
)
class FacebookIntegrationTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='facebook-owner',
            email='fb-owner@example.com',
            password='Strong-password-123!',
        )
        self.other_user = user_model.objects.create_user(
            username='facebook-other',
            email='fb-other@example.com',
            password='Strong-password-123!',
        )
        self.client.force_authenticate(self.user)
        # OAuth start/callback are regular Django views and need a real session.
        self.client.force_login(self.user)

    def create_account(self, user=None, token='user-access-token', expires_at=None, status_value='connected'):
        account = FacebookAccount(
            owner=user or self.user,
            facebook_user_id='fb-user-100',
            token_expires_at=expires_at or timezone.now() + timedelta(days=30),
            status=status_value,
            granted_permissions=['pages_show_list', 'pages_read_engagement', 'pages_manage_posts'],
        )
        account.set_access_token(token)
        account.save()
        return account

    def create_page(self, account=None, page_id='page-1', name='Example Page', selected=False):
        return FacebookPage.objects.create(
            account=account or self.create_account(),
            page_id=page_id,
            name=name,
            picture_url='https://example.com/page.jpg',
            selected=selected,
            page_access_token_encrypted=encrypt_token('page-access-token'),
        )

    def seed_oauth_session(self, state='known-oauth-state'):
        session = self.client.session
        session[OAUTH_STATE_SESSION_KEY] = state
        session[OAUTH_STATE_CREATED_SESSION_KEY] = time.time()
        session.save()
        return state

    def test_oauth_start_redirects_with_version_scopes_and_session_state(self):
        response = self.client.get('/api/facebook/oauth/start/')

        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response['Location'])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, 'www.facebook.com')
        self.assertIn('/v27.0/dialog/oauth', parsed.path)
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['scope'], ['pages_show_list,pages_read_engagement,pages_manage_posts'])
        self.assertTrue(query['state'][0])
        self.assertEqual(self.client.session[OAUTH_STATE_SESSION_KEY], query['state'][0])
        self.assertIn(OAUTH_STATE_CREATED_SESSION_KEY, self.client.session)

    def test_oauth_start_does_not_start_without_django_authentication(self):
        self.client.logout()

        response = self.client.get('/api/facebook/oauth/start/')

        self.assertRedirects(response, 'http://localhost:5173/?facebook=auth-required', fetch_redirect_response=False)

    def test_oauth_start_reports_missing_app_configuration_without_redirecting_to_meta(self):
        with override_settings(FACEBOOK_APP_SECRET=''):
            response = self.client.get('/api/facebook/oauth/start/')

        self.assertRedirects(response, 'http://localhost:5173/?facebook=configuration-error', fetch_redirect_response=False)

    def test_oauth_state_is_single_use_and_expires(self):
        session = {
            OAUTH_STATE_SESSION_KEY: 'expected-state',
            OAUTH_STATE_CREATED_SESSION_KEY: time.time(),
        }
        self.assertTrue(validate_oauth_state(session, 'expected-state'))
        self.assertFalse(validate_oauth_state(session, 'expected-state'))

        expired_session = {
            OAUTH_STATE_SESSION_KEY: 'expected-state',
            OAUTH_STATE_CREATED_SESSION_KEY: time.time() - 601,
        }
        self.assertFalse(validate_oauth_state(expired_session, 'expected-state'))

    @patch('facebook.views.get_authorized_pages')
    @patch('facebook.views.get_facebook_identity', return_value='fb-user-200')
    @patch('facebook.views.get_granted_permissions', return_value=['pages_read_engagement', 'pages_show_list', 'pages_manage_posts'])
    @patch('facebook.views.exchange_authorization_code')
    def test_successful_oauth_callback_saves_encrypted_tokens_and_pages(
        self,
        exchange,
        permissions,
        identity,
        pages,
    ):
        self.seed_oauth_session()
        expires_at = timezone.now() + timedelta(days=45)
        exchange.return_value = ('user-secret-token', expires_at)
        pages.return_value = [{
            'id': 'page-200',
            'name': 'Managed page',
            'category': 'Media',
            'picture': {'data': {'url': 'https://example.com/page-200.jpg'}},
            'tasks': ['CREATE_CONTENT', 'ANALYZE'],
            'access_token': 'page-secret-token',
        }]

        response = self.client.get(
            '/api/facebook/oauth/callback/',
            {'state': 'known-oauth-state', 'code': 'one-time-code'},
        )

        self.assertRedirects(response, 'http://localhost:5173/?facebook=connected', fetch_redirect_response=False)
        account = FacebookAccount.objects.get(owner=self.user)
        page = FacebookPage.objects.get(account=account, page_id='page-200')
        self.assertEqual(account.get_access_token(), 'user-secret-token')
        self.assertNotEqual(account.access_token_encrypted, 'user-secret-token')
        self.assertEqual(page.get_page_access_token(), 'page-secret-token')
        self.assertNotEqual(page.page_access_token_encrypted, 'page-secret-token')
        self.assertEqual(page.name, 'Managed page')
        self.assertTrue(page.available)
        exchange.assert_called_once_with('one-time-code')
        permissions.assert_called_once()
        identity.assert_called_once()
        pages.assert_called_once()

    @patch('facebook.views.get_authorized_pages')
    @patch('facebook.views.get_facebook_identity', return_value='fb-user-missing-permission')
    @patch('facebook.views.get_granted_permissions', return_value=['pages_show_list', 'pages_read_engagement'])
    @patch('facebook.views.exchange_authorization_code', return_value=('private-user-token', timezone.now() + timedelta(days=30)))
    def test_missing_publish_permission_is_stored_and_requires_reconnection(
        self, exchange, permissions, identity, pages,
    ):
        self.seed_oauth_session()
        response = self.client.get(
            '/api/facebook/oauth/callback/',
            {'state': 'known-oauth-state', 'code': 'code'},
        )
        self.assertRedirects(response, 'http://localhost:5173/?facebook=permissions-missing', fetch_redirect_response=False)
        account = FacebookAccount.objects.get(owner=self.user)
        self.assertEqual(account.status, FacebookAccount.Status.REVOKED)
        self.assertNotIn('pages_manage_posts', account.granted_permissions)
        self.assertEqual(account.get_access_token(), 'private-user-token')
        pages.assert_not_called()

    @patch('facebook.views.exchange_authorization_code')
    def test_invalid_callback_state_is_rejected_without_exchanging_code(self, exchange):
        self.seed_oauth_session()

        response = self.client.get(
            '/api/facebook/oauth/callback/',
            {'state': 'attacker-state', 'code': 'untrusted-code'},
        )

        self.assertRedirects(response, 'http://localhost:5173/?facebook=state-error', fetch_redirect_response=False)
        exchange.assert_not_called()
        self.assertFalse(FacebookAccount.objects.exists())
        self.assertNotIn(OAUTH_STATE_SESSION_KEY, self.client.session)

    @patch('facebook.views.exchange_authorization_code')
    def test_callback_without_code_is_rejected(self, exchange):
        self.seed_oauth_session()

        response = self.client.get('/api/facebook/oauth/callback/', {'state': 'known-oauth-state'})

        self.assertRedirects(response, 'http://localhost:5173/?facebook=callback-error', fetch_redirect_response=False)
        exchange.assert_not_called()

    @patch('facebook.views.exchange_authorization_code', side_effect=FacebookIntegrationError('Meta unavailable'))
    def test_meta_callback_failure_does_not_create_connected_account(self, exchange):
        self.seed_oauth_session()

        response = self.client.get(
            '/api/facebook/oauth/callback/',
            {'state': 'known-oauth-state', 'code': 'code'},
        )

        self.assertRedirects(response, 'http://localhost:5173/?facebook=connection-error', fetch_redirect_response=False)
        self.assertFalse(FacebookAccount.objects.exists())

    def test_token_encryption_round_trip_and_rejects_missing_key(self):
        encrypted = encrypt_token('never-return-this-token')
        self.assertNotEqual(encrypted, 'never-return-this-token')
        self.assertEqual(decrypt_token(encrypted), 'never-return-this-token')
        with override_settings(FACEBOOK_TOKEN_ENCRYPTION_KEY=''):
            with self.assertRaises(TokenEncryptionError):
                encrypt_token('token')

    @patch('facebook.oauth._request_json')
    def test_oauth_code_is_exchanged_server_side_then_extended(self, request_json):
        request_json.side_effect = [
            {'access_token': 'short-lived-token', 'expires_in': 3600},
            {'access_token': 'long-lived-token', 'expires_in': 5184000},
        ]

        token, expiry = exchange_authorization_code('one-use-code')

        self.assertEqual(token, 'long-lived-token')
        self.assertGreater(expiry, timezone.now())
        self.assertEqual(request_json.call_count, 2)
        first_call = request_json.call_args_list[0]
        self.assertEqual(first_call.args[0], 'oauth/access_token')
        self.assertEqual(first_call.args[1]['code'], 'one-use-code')
        self.assertEqual(first_call.args[1]['redirect_uri'], 'http://localhost:8000/api/facebook/oauth/callback/')
        self.assertEqual(request_json.call_args_list[1].args[1]['fb_exchange_token'], 'short-lived-token')
        self.assertEqual(request_json.call_args_list[1].args[1]['client_secret'], 'test-facebook-app-secret')

    @patch('facebook.oauth.graph_get')
    def test_graph_page_retrieval_follows_meta_cursor_without_following_external_urls(self, graph_get):
        graph_get.side_effect = [
            {
                'data': [{'id': 'page-a', 'name': 'First'}],
                'paging': {'next': 'https://evil.example/path', 'cursors': {'after': 'cursor-1'}},
            },
            {'data': [{'id': 'page-b', 'name': 'Second'}], 'paging': {}},
        ]

        pages = get_authorized_pages('secret-token', 'fb-user-100')

        self.assertEqual([page['id'] for page in pages], ['page-a', 'page-b'])
        self.assertEqual(graph_get.call_count, 2)
        self.assertNotIn('after', graph_get.call_args_list[0].args[2])
        self.assertEqual(graph_get.call_args_list[1].args[2]['after'], 'cursor-1')
        self.assertEqual(graph_get.call_args_list[0].args[0], 'fb-user-100/accounts')

    def test_page_sync_prevents_duplicates_and_marks_removed_pages_unavailable(self):
        account = self.create_account()
        page_data = [{
            'id': 'page-repeat',
            'name': 'Repeated page',
            'picture': {'data': {'url': 'https://example.com/repeated.jpg'}},
            'access_token': 'page-secret',
        }]

        save_authorized_pages(account, page_data)
        page = FacebookPage.objects.get(account=account, page_id='page-repeat')
        page.selected = True
        page.save(update_fields=['selected'])
        save_authorized_pages(account, page_data)
        save_authorized_pages(account, [])

        self.assertEqual(FacebookPage.objects.filter(account=account, page_id='page-repeat').count(), 1)
        page.refresh_from_db()
        self.assertFalse(page.available)
        self.assertFalse(page.selected)
        self.assertEqual(page.page_access_token_encrypted, '')

    def test_database_unique_constraint_rejects_duplicate_page_records(self):
        account = self.create_account()
        self.create_page(account, page_id='unique-page')

        with self.assertRaises(IntegrityError), transaction.atomic():
            FacebookPage.objects.create(account=account, page_id='unique-page', name='Duplicate')

    def test_page_list_never_exposes_user_or_page_access_tokens(self):
        account = self.create_account(token='user-private-token')
        page = self.create_page(account, page_id='safe-page')
        page.set_page_access_token('page-private-token')
        page.save(update_fields=['page_access_token_encrypted'])

        response = self.client.get('/api/facebook/pages/')
        response_text = str(response.data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn('access_token', response_text)
        self.assertNotIn('user-private-token', response_text)
        self.assertNotIn('page-private-token', response_text)
        self.assertNotIn(account.access_token_encrypted, response_text)
        self.assertNotIn(page.page_access_token_encrypted, response_text)

    def test_connection_status_expires_after_token_expiry(self):
        account = self.create_account(
            expires_at=timezone.now() - timedelta(minutes=1),
        )

        response = self.client.get('/api/facebook/connection/')

        account.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], FacebookAccount.Status.EXPIRED)
        self.assertFalse(response.data['connected'])
        self.assertEqual(account.status, FacebookAccount.Status.EXPIRED)
        self.assertNotIn('access_token', str(response.data))

    @patch('facebook.views.refresh_authorized_pages', side_effect=FacebookIntegrationError('Meta unavailable'))
    def test_refresh_returns_safe_error_for_meta_api_failure(self, refresh):
        self.create_account()

        response = self.client.post('/api/facebook/pages/refresh/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data['detail'], 'Meta unavailable')
        self.assertNotIn('access_token', str(response.data))

    @patch('facebook.views.refresh_authorized_pages', side_effect=FacebookTokenExpired('Reconnect'))
    def test_refresh_handles_expired_or_revoked_tokens(self, refresh):
        account = self.create_account()

        response = self.client.post('/api/facebook/pages/refresh/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['detail'], 'Reconnect')
        self.assertNotIn('access_token', str(response.data))

    @patch('facebook.views.refresh_authorized_pages', side_effect=FacebookPermissionMissing('Missing Page permission'))
    def test_refresh_handles_revoked_permissions(self, refresh):
        account = self.create_account()

        response = self.client.post('/api/facebook/pages/refresh/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['detail'], 'Missing Page permission')

    def test_missing_facebook_connection_cannot_refresh_pages(self):
        response = self.client.post('/api/facebook/pages/refresh/', {}, format='json')

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['detail'], 'Connect a Facebook account before refreshing Pages.')

    def test_page_selection_can_be_toggled_by_owner(self):
        account = self.create_account()
        page = self.create_page(account, page_id='selectable-page')
        url = f'/api/facebook/pages/{page.pk}/selection/'

        selected = self.client.post(url, {'selected': True}, format='json')
        deselected = self.client.post(url, {'selected': False}, format='json')

        page.refresh_from_db()
        self.assertEqual(selected.status_code, status.HTTP_200_OK)
        self.assertTrue(selected.data['selected'])
        self.assertFalse(deselected.data['selected'])
        self.assertFalse(page.selected)

    def test_cannot_select_page_for_expired_connection(self):
        account = self.create_account(status_value=FacebookAccount.Status.EXPIRED)
        page = self.create_page(account, page_id='expired-page')

        response = self.client.post(
            f'/api/facebook/pages/{page.pk}/selection/',
            {'selected': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        page.refresh_from_db()
        self.assertFalse(page.selected)

    def test_other_user_cannot_list_or_select_someone_elses_page(self):
        account = self.create_account()
        page = self.create_page(account, page_id='private-page')
        self.client.force_authenticate(self.other_user)

        listed = self.client.get('/api/facebook/pages/')
        selected = self.client.post(
            f'/api/facebook/pages/{page.pk}/selection/',
            {'selected': True},
            format='json',
        )

        page.refresh_from_db()
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data, [])
        self.assertEqual(selected.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(page.selected)

    def test_user_cannot_disconnect_someone_elses_facebook_connection(self):
        account = self.create_account()
        self.client.force_authenticate(self.other_user)

        response = self.client.delete('/api/facebook/connection/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['revocation_confirmed'])
        self.assertTrue(FacebookAccount.objects.filter(pk=account.pk).exists())

    @patch('facebook.views.revoke_facebook_permissions', return_value=True)
    def test_disconnect_revokes_page_scopes_and_removes_local_tokens_and_pages(self, revoke):
        account = self.create_account()
        page = self.create_page(account, page_id='disconnect-page')

        response = self.client.delete('/api/facebook/connection/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['disconnected'])
        self.assertTrue(response.data['revocation_confirmed'])
        self.assertFalse(FacebookAccount.objects.filter(pk=account.pk).exists())
        self.assertFalse(FacebookPage.objects.filter(pk=page.pk).exists())
        self.assertNotIn('access_token', str(response.data))
        revoke.assert_called_once_with('user-access-token', 'fb-user-100')

    @patch('facebook.views.revoke_facebook_permissions', side_effect=FacebookIntegrationError('Meta unavailable'))
    def test_disconnect_still_erases_local_credentials_if_remote_revoke_fails(self, revoke):
        account = self.create_account()

        response = self.client.delete('/api/facebook/connection/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['disconnected'])
        self.assertFalse(response.data['revocation_confirmed'])
        self.assertFalse(FacebookAccount.objects.filter(pk=account.pk).exists())

    def test_facebook_api_requires_authentication(self):
        self.client.force_authenticate(user=None)

        responses = [
            self.client.get('/api/facebook/connection/'),
            self.client.get('/api/facebook/pages/'),
            self.client.post('/api/facebook/pages/refresh/', {}, format='json'),
            self.client.delete('/api/facebook/connection/'),
        ]

        self.assertTrue(all(response.status_code == status.HTTP_403_FORBIDDEN for response in responses))

    def test_session_authenticated_selection_requires_csrf_token(self):
        account = self.create_account()
        page = self.create_page(account, page_id='csrf-page')
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)

        response = csrf_client.post(
            f'/api/facebook/pages/{page.pk}/selection/',
            {'selected': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        page.refresh_from_db()
        self.assertFalse(page.selected)

    @patch('facebook.services.get_authorized_pages', return_value=[])
    @patch('facebook.services.get_granted_permissions', return_value=['pages_show_list', 'pages_read_engagement', 'pages_manage_posts'])
    def test_refresh_saves_only_owner_pages(self, permissions, get_pages):
        account = self.create_account()
        get_pages.return_value = [{
            'id': 'refreshed-page',
            'name': 'Refreshed Page',
            'picture': {'data': {'url': 'https://example.com/refreshed.jpg'}},
            'tasks': ['ANALYZE'],
            'access_token': 'page-token',
        }]

        refreshed = refresh_authorized_pages(account)

        self.assertEqual(refreshed.count(), 1)
        page = refreshed.get()
        self.assertEqual(page.account.owner, self.user)
        self.assertEqual(page.page_id, 'refreshed-page')
        self.assertEqual(page.get_page_access_token(), 'page-token')
