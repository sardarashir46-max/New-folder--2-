"""Business rules shared by the content API and the publishing worker."""
from django.utils import timezone

from facebook.models import FacebookAccount
from facebook.oauth import FacebookIntegrationError
from facebook.services import usable_access_token

from .models import MediaAsset, ScheduledPost, ScheduledPostTarget


def approve_media_declaration(media, user, rights_status):
    """Record the uploader's rights declaration; this is not legal verification."""
    media.rights_status = rights_status
    if rights_status in (MediaAsset.RightsStatus.OWNED, MediaAsset.RightsStatus.AUTHORIZED):
        media.approved_at = timezone.now()
        media.approved_by = user
    else:
        media.approved_at = None
        media.approved_by = None
    media.save(update_fields=['rights_status', 'approved_at', 'approved_by', 'updated_at'])
    return media


def validate_target_is_still_authorized(target):
    """Recheck rights and Page access at execution time, after scheduling."""
    post = target.post
    media = post.media
    if media is None or media.owner_id != post.owner_id:
        return 'The scheduled video is no longer available. No post was sent.'
    if media.processing_status != MediaAsset.ProcessingStatus.READY:
        return 'The scheduled video is not ready. No post was sent.'
    if media.rights_status not in (MediaAsset.RightsStatus.OWNED, MediaAsset.RightsStatus.AUTHORIZED):
        return 'The video rights approval changed. Review the video before scheduling again.'

    page = target.page
    if page is None or page.account.owner_id != post.owner_id or not page.available or not page.selected:
        return 'The selected Facebook Page is no longer available. No post was sent.'
    account = page.account
    if account.status != FacebookAccount.Status.CONNECTED:
        return 'The Facebook connection needs attention. Reconnect it before scheduling again.'
    if account.token_expires_at and account.token_expires_at <= timezone.now():
        return 'The Facebook connection expired. Reconnect it before scheduling again.'
    try:
        # Decryption confirms local credentials are usable; no token is logged or sent.
        usable_access_token(account)
    except FacebookIntegrationError:
        return 'The Facebook connection is unavailable. Reconnect it before scheduling again.'
    return ''


class PublishingError(Exception):
    """Base class for safe publishing adapter errors; messages must be credential-free."""


class TemporaryPublishingError(PublishingError):
    """A temporary failure that the worker may retry with backoff."""


class PermanentPublishingError(PublishingError):
    """A permanent failure that must not be retried automatically."""


class FacebookPublishingService:
    """Safe adapter boundary. Meta publishing is deliberately disabled in this milestone."""

    def publish(self, target, idempotency_key):
        del target, idempotency_key
        raise PermanentPublishingError(
            'Facebook publishing is not enabled yet. No content was sent to Meta.'
        )
