"""Meta Facebook Login and versioned Graph API helpers."""
import hmac
import json
import re
import time
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone


OAUTH_STATE_SESSION_KEY = 'facebook_oauth_state'
OAUTH_STATE_CREATED_SESSION_KEY = 'facebook_oauth_state_created_at'
OAUTH_STATE_TTL_SECONDS = 600


class FacebookIntegrationError(Exception):
    """A safe, user-facing Facebook integration error."""


class FacebookTokenExpired(FacebookIntegrationError):
    """Facebook rejected or expired a previously valid token."""


class FacebookPermissionMissing(FacebookIntegrationError):
    """The user revoked one or more Page permissions from this app."""


class FacebookConfigurationError(FacebookIntegrationError):
    """Required Meta app or token-encryption settings are missing or invalid."""


def validate_oauth_state(session, provided_state):
    """Consume and validate one short-lived OAuth state value from the session."""
    expected_state = session.pop(OAUTH_STATE_SESSION_KEY, None)
    created_at = session.pop(OAUTH_STATE_CREATED_SESSION_KEY, None)
    if not isinstance(expected_state, str) or not isinstance(provided_state, str):
        return False
    if not isinstance(created_at, (int, float)) or time.time() - created_at > OAUTH_STATE_TTL_SECONDS:
        return False
    if time.time() - created_at < 0:
        return False
    return hmac.compare_digest(expected_state, provided_state)


def _graph_root():
    version = settings.FACEBOOK_GRAPH_API_VERSION
    if not re.fullmatch(r'v\d+\.\d+', version):
        raise FacebookConfigurationError('FACEBOOK_GRAPH_API_VERSION must look like v27.0.')
    return f'https://graph.facebook.com/{version}'


def _require_app_settings():
    if not settings.FACEBOOK_APP_ID or not settings.FACEBOOK_APP_SECRET:
        raise FacebookConfigurationError(
            'Facebook Login is not configured. Set FACEBOOK_APP_ID and FACEBOOK_APP_SECRET.'
        )
    if not settings.FACEBOOK_REDIRECT_URI:
        raise FacebookConfigurationError('Set FACEBOOK_REDIRECT_URI to the registered Meta callback URL.')
    return _graph_root()


def build_authorization_url(state):
    graph_root = _require_app_settings()
    query = urlencode({
        'client_id': settings.FACEBOOK_APP_ID,
        'redirect_uri': settings.FACEBOOK_REDIRECT_URI,
        'state': state,
        'response_type': 'code',
        'scope': ','.join(settings.FACEBOOK_LOGIN_SCOPES),
    })
    return f'https://www.facebook.com/{settings.FACEBOOK_GRAPH_API_VERSION}/dialog/oauth?{query}'


def _request_json(endpoint, params, method='GET'):
    graph_root = _graph_root()
    url = f'{graph_root}/{endpoint.lstrip("/")}?{urlencode(params)}'
    request = Request(url, headers={'Accept': 'application/json'}, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read().decode('utf-8')
        data = json.loads(payload)
    except HTTPError as error:
        try:
            error_body = json.loads(error.read().decode('utf-8'))
            code = error_body.get('error', {}).get('code')
        except (ValueError, AttributeError, TypeError):
            code = None
        if code == 190:
            raise FacebookTokenExpired(
                'Facebook authorization expired or was revoked. Reconnect the account.'
            ) from None
        raise FacebookIntegrationError(
            'Meta could not complete the request. Check Page access and app permissions, then retry.'
        ) from None
    except (URLError, TimeoutError, ValueError, UnicodeDecodeError):
        raise FacebookIntegrationError(
            'Could not reach Meta. Please retry in a moment.'
        ) from None
    if isinstance(data, bool) and method == 'DELETE':
        return data
    if not isinstance(data, dict):
        raise FacebookIntegrationError('Meta returned an unexpected response. Please retry.')
    if 'error' in data:
        error_code = data.get('error', {}).get('code')
        if error_code == 190:
            raise FacebookTokenExpired(
                'Facebook authorization expired or was revoked. Reconnect the account.'
            )
        raise FacebookIntegrationError(
            'Meta could not complete the request. Check Page access and app permissions, then retry.'
        )
    return data


def exchange_authorization_code(code):
    """Exchange a one-use OAuth code server-side and extend its token lifetime."""
    _require_app_settings()
    token_data = _request_json('oauth/access_token', {
        'client_id': settings.FACEBOOK_APP_ID,
        'redirect_uri': settings.FACEBOOK_REDIRECT_URI,
        'client_secret': settings.FACEBOOK_APP_SECRET,
        'code': code,
    })
    short_lived_token = token_data.get('access_token')
    if not isinstance(short_lived_token, str) or not short_lived_token:
        raise FacebookIntegrationError('Meta did not return an access token. Please reconnect.')

    long_lived = _request_json('oauth/access_token', {
        'grant_type': 'fb_exchange_token',
        'client_id': settings.FACEBOOK_APP_ID,
        'client_secret': settings.FACEBOOK_APP_SECRET,
        'fb_exchange_token': short_lived_token,
    })
    access_token = long_lived.get('access_token')
    if not isinstance(access_token, str) or not access_token:
        raise FacebookIntegrationError('Meta did not return a usable access token. Please reconnect.')
    expires_in = long_lived.get('expires_in', token_data.get('expires_in'))
    try:
        expires_at = timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
    except (TypeError, ValueError, OverflowError):
        expires_at = None
    return access_token, expires_at


def graph_get(endpoint, access_token, params=None):
    request_params = dict(params or {})
    request_params['access_token'] = access_token
    return _request_json(endpoint, request_params)


def graph_delete(endpoint, access_token):
    return _request_json(
        endpoint,
        {'access_token': access_token},
        method='DELETE',
    )


def get_facebook_identity(access_token):
    data = graph_get('me', access_token, {'fields': 'id'})
    facebook_user_id = data.get('id')
    if not isinstance(facebook_user_id, str) or not facebook_user_id:
        raise FacebookIntegrationError('Meta did not return an account identifier. Please reconnect.')
    return facebook_user_id


def get_granted_permissions(access_token):
    data = graph_get('me/permissions', access_token)
    granted = {
        item.get('permission')
        for item in data.get('data', [])
        if item.get('status') == 'granted'
    }
    return sorted(granted)


def get_authorized_pages(access_token, facebook_user_id):
    """Read the Page accounts edge, following cursors on Meta's fixed Graph host."""
    endpoint = f'{quote(facebook_user_id, safe="")}/accounts'
    params = {
        'fields': 'id,name,category,picture,access_token,tasks',
        'limit': 100,
    }
    pages = []
    seen_cursors = set()
    for _page_number in range(100):
        # Copy the query so pagination cannot mutate the first request's parameters.
        response = graph_get(endpoint, access_token, dict(params))
        items = response.get('data', [])
        if not isinstance(items, list):
            raise FacebookIntegrationError('Meta returned an unexpected Pages response.')
        pages.extend(item for item in items if isinstance(item, dict) and item.get('id') and item.get('name'))
        paging = response.get('paging') or {}
        cursor = (paging.get('cursors') or {}).get('after')
        if not paging.get('next') or not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
        params['after'] = cursor
    return pages


def revoke_facebook_permissions(access_token, facebook_user_id):
    """Attempt to revoke only the Page permissions requested by this integration."""
    encoded_user_id = quote(facebook_user_id, safe='')
    for permission in settings.FACEBOOK_LOGIN_SCOPES:
        result = graph_delete(f'{encoded_user_id}/permissions/{quote(permission, safe="")}', access_token)
        if result is not True and result.get('success') is not True:
            return False
    return True
