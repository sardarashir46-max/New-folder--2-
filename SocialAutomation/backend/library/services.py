"""Business rules and official server-side Meta video publishing adapter."""
import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from facebook.models import FacebookAccount
from facebook.oauth import FacebookIntegrationError, FacebookTokenExpired, graph_get
from facebook.services import usable_access_token

from .models import MediaAsset, ScheduledPost, ScheduledPostTarget

REQUIRED_PUBLISH_PERMISSIONS = {'pages_show_list', 'pages_read_engagement', 'pages_manage_posts'}


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
    if not REQUIRED_PUBLISH_PERMISSIONS.issubset(set(account.granted_permissions or [])):
        return 'Facebook publishing permissions are missing. Reconnect Facebook and approve pages_manage_posts.'
    if 'CREATE_CONTENT' not in page.tasks:
        return 'This Facebook Page does not grant CREATE_CONTENT. Choose another Page or update Page access.'
    try:
        # Decryption confirms local credentials are usable; no token is logged or sent.
        usable_access_token(account)
        page_token = page.get_page_access_token()
        if not page_token:
            return 'The Facebook Page token is unavailable. Refresh Page access or reconnect Facebook.'
        page_identity = graph_get(page.page_id, page_token, {'fields': 'id'}).get('id')
        if str(page_identity or '') != page.page_id:
            return 'Facebook no longer confirms access to this Page. Refresh Page access or reconnect.'
    except FacebookTokenExpired:
        return 'The Facebook token expired. Reconnect Facebook before publishing.'
    except FacebookIntegrationError:
        return 'The Facebook connection is unavailable. Reconnect it before scheduling again.'
    return ''


class PublishingError(Exception):
    """Base class for safe publishing adapter errors; messages must be credential-free."""


class TemporaryPublishingError(PublishingError):
    """A temporary failure that the worker may retry with backoff."""


class PermanentPublishingError(PublishingError):
    """A permanent failure that must not be retried automatically."""


class UncertainPublishingError(PublishingError):
    """Meta may have accepted a publish request; automatic retry could duplicate it."""


class FacebookPublishingService:
    """Upload private video files through Meta's official Page Videos chunked API."""

    CHUNK_SIZE = 8 * 1024 * 1024

    def _post(self, endpoint, fields, file_field=None, file_chunk=None):
        version = settings.FACEBOOK_GRAPH_API_VERSION
        boundary = f'----SocialAutomation{uuid.uuid4().hex}'
        body = bytearray()
        for key, value in fields.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        if file_field:
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="chunk.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
            body.extend(file_chunk)
            body.extend(b'\r\n')
        body.extend(f'--{boundary}--\r\n'.encode())
        host = 'graph-video.facebook.com' if file_field else 'graph.facebook.com'
        request = Request(f'https://{host}/{version}/{endpoint.lstrip("/")}', data=bytes(body), headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'Accept': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode('utf-8'))
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode('utf-8'))
                detail = payload.get('error', {})
                code = detail.get('code')
            except (ValueError, AttributeError, TypeError):
                code = None
            if code == 190:
                raise PermanentPublishingError('The Facebook Page token expired or was revoked. Reconnect Facebook.') from None
            if code in (4, 17, 32, 613, 80004):
                raise TemporaryPublishingError('Meta rate limited the publishing request. Retry later.') from None
            if code in (200, 10):
                raise PermanentPublishingError('Meta denied publishing. Check app access, Page tasks and permissions.') from None
            if code in (6000, 6001, 382, 100):
                raise PermanentPublishingError('Meta rejected the video or upload parameters. Check the media format.') from None
            raise TemporaryPublishingError('Meta video upload failed temporarily.') from None
        except (URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
            # A failure during finish is treated as uncertain by the caller.
            raise
        if not isinstance(data, dict) or data.get('error'):
            error_data = data.get('error', {}) if isinstance(data, dict) else {}
            if error_data.get('code') in (200, 10):
                raise PermanentPublishingError('Meta denied publishing. Check app access, Page tasks and permissions.')
            raise PermanentPublishingError('Meta returned an invalid publishing response.')
        return data

    def publish(self, target, idempotency_key):
        del idempotency_key
        if not settings.PUBLISHING_ENABLED:
            raise PermanentPublishingError('Facebook publishing is disabled by configuration.')
        media = target.post.media
        if not media or not media.file:
            raise PermanentPublishingError('The private video file is unavailable.')
        page = target.page
        try:
            token = page.get_page_access_token()
            if not token:
                raise PermanentPublishingError('The Facebook Page token is unavailable. Reconnect Facebook.')
            size = media.file.size
            start = self._post(f'{page.page_id}/videos', {
                'access_token': token, 'upload_phase': 'start', 'file_size': size,
            })
            session_id = start.get('upload_session_id')
            video_id = start.get('video_id')
            offset = int(start.get('start_offset', 0))
            end = int(start.get('end_offset', size))
            if not session_id or not video_id or offset < 0 or end < offset or end > size:
                raise PermanentPublishingError('Meta returned an invalid video upload session.')
            with media.file.open('rb') as stream:
                while offset < size:
                    stream.seek(offset)
                    chunk = stream.read(min(self.CHUNK_SIZE, size - offset))
                    if not chunk:
                        raise PermanentPublishingError('The private video file ended unexpectedly.')
                    result = self._post(f'{page.page_id}/videos', {
                        'access_token': token, 'upload_phase': 'transfer',
                        'upload_session_id': session_id, 'start_offset': offset,
                    }, file_field='video_file_chunk', file_chunk=chunk)
                    next_offset = int(result.get('start_offset', end))
                    next_end = int(result.get('end_offset', size))
                    if next_offset <= offset or next_end < next_offset or next_end > size:
                        raise PermanentPublishingError('Meta returned invalid video upload offsets.')
                    offset, end = next_offset, next_end
            try:
                result = self._post(f'{page.page_id}/videos', {
                    'access_token': token, 'upload_phase': 'finish',
                    'upload_session_id': session_id,
                    'title': (target.post.media_title_snapshot or media.title)[:255],
                    'description': '\n\n'.join(value for value in (target.post.caption, target.post.description) if value),
                    'published': 'true',
                })
            except (TemporaryPublishingError, URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError):
                raise UncertainPublishingError('Meta may have received this video. Review Page activity before retrying.') from None
            external_id = result.get('video_id') or result.get('id')
            if result.get('success') is not True or not isinstance(external_id, (str, int)):
                raise PermanentPublishingError('Meta did not confirm video publication.')
            return {'confirmed': True, 'external_post_id': str(external_id)}
        except PermanentPublishingError:
            raise
        except (FacebookIntegrationError, OSError, ValueError, TypeError):
            raise TemporaryPublishingError('The Meta video upload failed temporarily.') from None
