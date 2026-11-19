"""Database synchronization for Facebook account and Page metadata."""
from django.db import transaction
from django.utils import timezone

from .models import FacebookAccount, FacebookPage
from .oauth import (
    FacebookIntegrationError,
    FacebookPermissionMissing,
    FacebookTokenExpired,
    get_authorized_pages,
    get_facebook_identity,
    get_granted_permissions,
)
from .tokens import TokenEncryptionError


def account_for_user(user):
    return FacebookAccount.objects.filter(owner=user).first()


def usable_access_token(account):
    if account.status != FacebookAccount.Status.CONNECTED:
        raise FacebookTokenExpired('Facebook authorization needs attention. Reconnect your account.')
    if account.token_expires_at and account.token_expires_at <= timezone.now():
        account.status = FacebookAccount.Status.EXPIRED
        account.save(update_fields=['status', 'updated_at'])
        raise FacebookTokenExpired('Facebook authorization expired. Reconnect your account.')
    try:
        return account.get_access_token()
    except TokenEncryptionError:
        account.status = FacebookAccount.Status.ERROR
        account.save(update_fields=['status', 'updated_at'])
        raise FacebookIntegrationError(
            'The stored Facebook authorization is unavailable. Reconnect your account.'
        ) from None


def _page_values(item, token_expires_at):
    picture = item.get('picture') or {}
    picture_data = picture.get('data') if isinstance(picture, dict) else {}
    picture_url = picture_data.get('url', '') if isinstance(picture_data, dict) else ''
    token = item.get('access_token')
    return {
        'name': item['name'][:255],
        'category': str(item.get('category') or '')[:255],
        'picture_url': picture_url or '',
        'tasks': item.get('tasks') if isinstance(item.get('tasks'), list) else [],
        'available': True,
        'page_access_token_encrypted': '',
        'page_token_expires_at': token_expires_at if token else None,
        'last_refreshed_at': timezone.now(),
    }, token


def save_authorized_pages(account, page_items):
    """Upsert page data; old pages no longer granted by Meta become unavailable."""
    now = timezone.now()
    seen_page_ids = set()
    with transaction.atomic():
        account.status = FacebookAccount.Status.CONNECTED
        account.save(update_fields=['status', 'updated_at'])
        for item in page_items:
            page_id = str(item['id'])
            seen_page_ids.add(page_id)
            defaults, page_token = _page_values(item, account.token_expires_at)
            page, _created = FacebookPage.objects.update_or_create(
                account=account,
                page_id=page_id,
                defaults=defaults,
            )
            page.set_page_access_token(page_token or '')
            page.updated_at = now
            page.save(update_fields=[
                'page_access_token_encrypted',
                'page_token_expires_at',
                'updated_at',
            ])

        removed_pages = FacebookPage.objects.filter(account=account, available=True)
        if seen_page_ids:
            removed_pages = removed_pages.exclude(page_id__in=seen_page_ids)
        removed_pages.update(
            available=False,
            selected=False,
            page_access_token_encrypted='',
            page_token_expires_at=None,
            updated_at=now,
        )
    return FacebookPage.objects.filter(account=account, available=True)


def complete_facebook_connection(user, facebook_user_id, access_token, expires_at, permissions, pages):
    """Save OAuth credentials only after identity, permissions and Page retrieval succeed."""
    with transaction.atomic():
        account, created = FacebookAccount.objects.get_or_create(
            owner=user,
            defaults={
                'facebook_user_id': facebook_user_id,
                'token_expires_at': expires_at,
                'status': FacebookAccount.Status.CONNECTED,
                'granted_permissions': permissions,
            },
        )
        account.facebook_user_id = facebook_user_id
        account.set_access_token(access_token)
        account.token_expires_at = expires_at
        account.status = FacebookAccount.Status.CONNECTED
        account.granted_permissions = permissions
        if not created:
            account.connected_at = timezone.now()
        account.save()
        return save_authorized_pages(account, pages)


def refresh_authorized_pages(account):
    access_token = usable_access_token(account)
    try:
        # Check that the Page permissions still exist before relying on them.
        permissions = get_granted_permissions(access_token)
        pages = get_authorized_pages(access_token, account.facebook_user_id)
    except FacebookTokenExpired:
        account.status = FacebookAccount.Status.EXPIRED
        account.save(update_fields=['status', 'updated_at'])
        raise
    except FacebookPermissionMissing:
        account.status = FacebookAccount.Status.REVOKED
        account.save(update_fields=['status', 'updated_at'])
        raise
    account.granted_permissions = permissions
    account.save(update_fields=['granted_permissions', 'updated_at'])
    return save_authorized_pages(account, pages)
