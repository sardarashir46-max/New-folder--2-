import secrets
import time

from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FacebookAccount, FacebookPage
from .oauth import (
    FacebookConfigurationError,
    FacebookIntegrationError,
    FacebookPermissionMissing,
    FacebookTokenExpired,
    OAUTH_STATE_CREATED_SESSION_KEY,
    OAUTH_STATE_SESSION_KEY,
    build_authorization_url,
    exchange_authorization_code,
    get_authorized_pages,
    get_facebook_identity,
    get_granted_permissions,
    revoke_facebook_permissions,
    validate_oauth_state,
)
from .serializers import FacebookConnectionSerializer, FacebookPageSerializer
from .services import (
    account_for_user,
    complete_facebook_connection,
    refresh_authorized_pages,
    usable_access_token,
)
from .tokens import TokenEncryptionError, validate_token_encryption_configuration


def _frontend_result(result):
    frontend_url = settings.FACEBOOK_FRONTEND_URL.rstrip('/')
    query = urlencode({'facebook': result})
    response = HttpResponseRedirect(f'{frontend_url}/?{query}')
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_GET
def oauth_start(request):
    if not request.user.is_authenticated:
        return _frontend_result('auth-required')
    try:
        validate_token_encryption_configuration()
        state = secrets.token_urlsafe(32)
        authorization_url = build_authorization_url(state)
    except (FacebookConfigurationError, TokenEncryptionError):
        return _frontend_result('configuration-error')

    request.session[OAUTH_STATE_SESSION_KEY] = state
    request.session[OAUTH_STATE_CREATED_SESSION_KEY] = time.time()
    response = HttpResponseRedirect(authorization_url)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@require_GET
def oauth_callback(request):
    state_values = request.GET.getlist('state')
    state = state_values[0] if len(state_values) == 1 else None
    if not validate_oauth_state(request.session, state):
        return _frontend_result('state-error')
    if not request.user.is_authenticated:
        return _frontend_result('auth-required')
    if request.GET.get('error'):
        # Do not reflect Meta's error_description, which is untrusted input.
        return _frontend_result('cancelled')
    code_values = request.GET.getlist('code')
    if len(code_values) != 1 or not code_values[0]:
        return _frontend_result('callback-error')

    try:
        access_token, expires_at = exchange_authorization_code(code_values[0])
        permissions = get_granted_permissions(access_token)
        facebook_user_id = get_facebook_identity(access_token)
        pages = get_authorized_pages(access_token, facebook_user_id)
        complete_facebook_connection(
            request.user,
            facebook_user_id,
            access_token,
            expires_at,
            permissions,
            pages,
        )
    except (FacebookIntegrationError, TokenEncryptionError):
        return _frontend_result('connection-error')
    return _frontend_result('connected')


class FacebookConnectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account = account_for_user(request.user)
        if account is None:
            return Response({'connected': False, 'status': FacebookAccount.Status.REVOKED})
        if (
            account.status == FacebookAccount.Status.CONNECTED
            and account.token_expires_at
            and account.token_expires_at <= timezone.now()
        ):
            account.status = FacebookAccount.Status.EXPIRED
            account.save(update_fields=['status', 'updated_at'])
        data = FacebookConnectionSerializer(account).data
        return Response({'connected': account.status == FacebookAccount.Status.CONNECTED, **data})

    def delete(self, request):
        account = account_for_user(request.user)
        if account is None:
            return Response({'disconnected': True, 'revocation_confirmed': False})

        revocation_confirmed = False
        try:
            access_token = account.get_access_token()
            revocation_confirmed = revoke_facebook_permissions(
                access_token,
                account.facebook_user_id,
            )
        except (FacebookIntegrationError, TokenEncryptionError):
            # Local token and Page records are still removed even if Meta cannot revoke them.
            pass
        account.delete()
        return Response({
            'disconnected': True,
            'revocation_confirmed': revocation_confirmed,
        })


class FacebookPagesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        pages = FacebookPage.objects.filter(
            account__owner=request.user,
            available=True,
        ).select_related('account')
        return Response(FacebookPageSerializer(pages, many=True).data)


class FacebookPagesRefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        account = account_for_user(request.user)
        if account is None:
            return Response(
                {'detail': 'Connect a Facebook account before refreshing Pages.'},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            pages = refresh_authorized_pages(account)
        except FacebookTokenExpired as error:
            return Response({'detail': str(error)}, status=status.HTTP_409_CONFLICT)
        except FacebookPermissionMissing as error:
            return Response({'detail': str(error)}, status=status.HTTP_409_CONFLICT)
        except FacebookIntegrationError as error:
            return Response({'detail': str(error)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({
            'pages': FacebookPageSerializer(pages, many=True).data,
            'connection': FacebookConnectionSerializer(account).data,
        })


class FacebookPageSelectionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, page_id):
        selected = request.data.get('selected')
        if not isinstance(selected, bool):
            return Response(
                {'detail': 'selected must be true or false.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        page = FacebookPage.objects.filter(
            pk=page_id,
            account__owner=request.user,
            available=True,
        ).first()
        if page is None:
            return Response({'detail': 'Page not found.'}, status=status.HTTP_404_NOT_FOUND)
        if selected and page.account.status != FacebookAccount.Status.CONNECTED:
            return Response(
                {'detail': 'Reconnect Facebook before selecting a Page.'},
                status=status.HTTP_409_CONFLICT,
            )
        page.selected = selected
        page.save(update_fields=['selected', 'updated_at'])
        return Response(FacebookPageSerializer(page).data)
