import tempfile
import uuid
from io import BytesIO
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from cryptography.fernet import Fernet
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from facebook.models import FacebookAccount, FacebookPage
from facebook.oauth import FacebookTokenExpired

from .models import MediaAsset, PublishingAttempt, ScheduledPost, ScheduledPostTarget
from .services import (
    FacebookPublishingService,
    PermanentPublishingError,
    TemporaryPublishingError,
    UncertainPublishingError,
    validate_target_is_still_authorized,
)
from .tasks import dispatch_due_scheduled_posts, process_scheduled_post, recover_stale_processing_targets


TEST_FERNET_KEY = Fernet.generate_key().decode('ascii')
MP4_CONTENT = b'\x00\x00\x00\x18ftypisom' + b'video payload for validation'


@override_settings(
    FACEBOOK_TOKEN_ENCRYPTION_KEY=TEST_FERNET_KEY,
    MEDIA_MAX_UPLOAD_BYTES=1024 * 1024,
    PUBLISHING_MAX_RETRIES=1,
    PUBLISHING_ENABLED=True,
    PUBLISHING_DISPATCH_ENABLED=True,
)
class ContentLibraryAndSchedulingTests(APITestCase):
    def setUp(self):
        self.graph_token_check = patch('library.services.graph_get', side_effect=lambda endpoint, *args, **kwargs: {'id': endpoint})
        self.graph_token_check.start()
        self.addCleanup(self.graph_token_check.stop)
        self.publisher_safety = patch(
            'library.tasks.FacebookPublishingService.publish',
            side_effect=PermanentPublishingError('Mocked Meta denial.'),
        )
        self.publisher_safety.start()
        self.addCleanup(self.publisher_safety.stop)
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='library-owner',
            email='library-owner@example.com',
            password='Strong-password-123!',
        )
        self.other_user = user_model.objects.create_user(
            username='other-library-user',
            email='other-library@example.com',
            password='Strong-password-123!',
        )
        self.page_counter = 0
        self.client.force_authenticate(self.user)
        self.temporary_media_root = tempfile.TemporaryDirectory()
        self.media_settings = override_settings(MEDIA_ROOT=Path(self.temporary_media_root.name))
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.addCleanup(self.temporary_media_root.cleanup)

    def upload(self, user=None, name='../original-video.mp4', content=MP4_CONTENT):
        from rest_framework.test import APIClient

        client = self.client if user in (None, self.user) else APIClient()
        if user not in (None, self.user):
            client.force_authenticate(user)
        response = client.post('/api/library/media/', {
            'file': SimpleUploadedFile(name, content, content_type='text/plain'),
            'title': 'Original clip',
            'description': 'A user supplied description',
            'caption': 'A publishing caption',
        }, format='multipart')
        return response

    def make_media(self, rights=MediaAsset.RightsStatus.OWNED):
        upload_response = self.upload()
        self.assertEqual(upload_response.status_code, status.HTTP_201_CREATED, upload_response.data)
        media = MediaAsset.objects.get(pk=upload_response.data['id'])
        if rights != MediaAsset.RightsStatus.PENDING:
            media.rights_status = rights
            media.approved_at = timezone.now()
            media.approved_by = self.user
            media.save(update_fields=['rights_status', 'approved_at', 'approved_by'])
        return media

    def make_page(self, user=None, selected=True, account_status=FacebookAccount.Status.CONNECTED, account=None):
        user = user or self.user
        self.page_counter += 1
        if account is None:
            account = FacebookAccount(
                owner=user,
                facebook_user_id=f'fb-user-{user.pk}',
                token_expires_at=timezone.now() + timedelta(days=20),
                status=account_status,
                granted_permissions=['pages_show_list', 'pages_read_engagement', 'pages_manage_posts'],
            )
            account.set_access_token('private-user-token')
            account.save()
        page = FacebookPage.objects.create(
            account=account,
            page_id=f'page-{user.pk}-{self.page_counter}',
            name=f'Owner Page {self.page_counter}',
            selected=selected,
            tasks=['CREATE_CONTENT'],
            page_access_token_encrypted='',
        )
        page.set_page_access_token('private-page-token')
        page.save(update_fields=['page_access_token_encrypted'])
        return page

    def schedule(self, media=None, pages=None, user=None, scheduled_for=None, key=None):
        media = media or self.make_media()
        pages = pages or [self.make_page(user=user)]
        payload = {
            'media': media.pk,
            'caption': 'A post caption',
            'description': 'Post details',
            'scheduled_for': (scheduled_for or timezone.now() + timedelta(hours=1)).isoformat(),
            'target_pages': [page.pk for page in pages],
            'idempotency_key': str(key or uuid.uuid4()),
        }
        response = self.client.post('/api/publishing/posts/', payload, format='json')
        return response

    def test_upload_checks_file_extension_and_actual_container_signature(self):
        invalid_extension = self.upload(name='video.exe')
        forged_extension = self.upload(name='not-a-video.mp4', content=b'plain text is not a video')

        self.assertEqual(invalid_extension.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(forged_extension.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MediaAsset.objects.count(), 0)

    def test_webm_and_mkv_extensions_must_match_the_ebml_document_type(self):
        valid_webm = b'\x1a\x45\xdf\xa3' + (b'\x42\x82\x84webm' + b' ' * 20)
        valid_mkv = b'\x1a\x45\xdf\xa3' + (b'\x42\x82\x88matroska' + b' ' * 20)
        webm_response = self.upload(name='clip.webm', content=valid_webm)
        mkv_response = self.upload(name='clip.mkv', content=valid_mkv)
        mismatch_response = self.upload(name='wrong-extension.webm', content=valid_mkv)

        self.assertEqual(webm_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mkv_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mismatch_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_limit_and_empty_file_are_rejected(self):
        with override_settings(MEDIA_MAX_UPLOAD_BYTES=8):
            too_large = self.upload()
        empty = self.upload(name='empty.mp4', content=b'')

        self.assertEqual(too_large.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)

    def test_uploaded_media_uses_private_random_path_and_requires_owned_file_endpoint(self):
        response = self.upload()
        media = MediaAsset.objects.get(pk=response.data['id'])
        stored_path = Path(media.file.name)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(media.rights_status, MediaAsset.RightsStatus.PENDING)
        self.assertIsNone(media.approved_at)
        self.assertEqual(media.detected_content_type, 'video/mp4')
        self.assertTrue(media.file.name.startswith(f'private-media/user-{self.user.pk}/'))
        self.assertNotIn('original-video', media.file.name)
        self.assertEqual(len(stored_path.name.split('.')[0]), 32)
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(f'/api/library/media/{media.pk}/').status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f'/api/library/media/{media.pk}/file/').status_code, status.HTTP_404_NOT_FOUND)

    def test_media_file_stream_is_private_and_uses_detected_content_type(self):
        response = self.upload()
        media = MediaAsset.objects.get(pk=response.data['id'])

        file_response = self.client.get(f'/api/library/media/{media.pk}/file/')

        self.assertEqual(file_response.status_code, status.HTTP_200_OK)
        self.assertEqual(file_response['Content-Type'], 'video/mp4')
        self.assertEqual(file_response['X-Content-Type-Options'], 'nosniff')
        self.assertIn(b'video payload', b''.join(file_response.streaming_content))

    def test_media_list_is_owner_scoped_and_search_filter_works(self):
        own = self.upload()
        self.upload(user=self.other_user)

        response = self.client.get('/api/library/media/?search=Original&rights_status=pending_review')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [own.data['id']])

    def test_only_owner_can_declare_rights_and_approval_is_a_user_declaration(self):
        media = self.make_media(rights=MediaAsset.RightsStatus.PENDING)
        self.client.force_authenticate(self.other_user)
        denied = self.client.patch(f'/api/library/media/{media.pk}/rights/', {'rights_status': 'owned'}, format='json')
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(self.user)
        approved = self.client.patch(f'/api/library/media/{media.pk}/rights/', {'rights_status': 'authorized'}, format='json')
        media.refresh_from_db()
        self.assertEqual(approved.status_code, status.HTTP_200_OK)
        self.assertEqual(media.rights_status, MediaAsset.RightsStatus.AUTHORIZED)
        self.assertEqual(media.approved_by, self.user)
        self.assertIsNotNone(media.approved_at)
        self.assertNotIn('verified', str(approved.data).lower())

    def test_rejected_rights_clear_approval_information(self):
        media = self.make_media()

        response = self.client.patch(f'/api/library/media/{media.pk}/rights/', {'rights_status': 'rejected'}, format='json')
        media.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(media.rights_status, MediaAsset.RightsStatus.REJECTED)
        self.assertIsNone(media.approved_at)
        self.assertIsNone(media.approved_by)

    def test_only_owned_or_authorized_media_can_be_scheduled(self):
        media = self.make_media(rights=MediaAsset.RightsStatus.PENDING)
        page = self.make_page()

        response = self.schedule(media=media, pages=[page])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ScheduledPost.objects.count(), 0)

    def test_schedule_requires_future_time_selected_page_and_valid_connection(self):
        media = self.make_media()
        page = self.make_page(selected=False)
        not_selected = self.schedule(media=media, pages=[page])
        page.selected = True
        page.save(update_fields=['selected'])
        page.account.status = FacebookAccount.Status.EXPIRED
        page.account.save(update_fields=['status'])
        expired_connection = self.schedule(media=media, pages=[page])
        page.account.status = FacebookAccount.Status.CONNECTED
        page.account.save(update_fields=['status'])
        past_time = self.schedule(media=media, pages=[page], scheduled_for=timezone.now() - timedelta(minutes=1))

        self.assertEqual(not_selected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(expired_connection.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(past_time.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ScheduledPost.objects.count(), 0)

    def test_cannot_schedule_for_another_users_page(self):
        media = self.make_media()
        other_page = self.make_page(user=self.other_user)

        response = self.schedule(media=media, pages=[other_page])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ScheduledPost.objects.count(), 0)

    def test_duplicate_page_ids_are_rejected_and_database_prevents_duplicate_targets(self):
        media = self.make_media()
        page = self.make_page()
        response = self.schedule(media=media, pages=[page, page])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        post = ScheduledPost.objects.create(owner=self.user, media=media, media_title_snapshot=media.title, scheduled_for=timezone.now() + timedelta(hours=1))
        ScheduledPostTarget.objects.create(post=post, page=page, page_id_snapshot=page.page_id, page_name_snapshot=page.name)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ScheduledPostTarget.objects.create(post=post, page=page, page_id_snapshot=page.page_id, page_name_snapshot=page.name)

    def test_request_idempotency_key_returns_the_existing_post_not_a_duplicate(self):
        media = self.make_media()
        page = self.make_page()
        key = uuid.uuid4()
        scheduled_for = timezone.now() + timedelta(hours=1)
        first = self.schedule(media=media, pages=[page], key=key, scheduled_for=scheduled_for)
        repeated = self.schedule(media=media, pages=[page], key=key, scheduled_for=scheduled_for)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(repeated.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data['id'], repeated.data['id'])
        self.assertEqual(ScheduledPost.objects.count(), 1)
        self.assertEqual(ScheduledPostTarget.objects.count(), 1)

    def test_pending_post_can_be_edited_and_cancelled_but_completed_attempts_cannot_be_edited(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post_id = created.data['id']

        edit = self.client.patch(f'/api/publishing/posts/{post_id}/', {'caption': 'Updated caption'}, format='json')
        cancelled = self.client.post(f'/api/publishing/posts/{post_id}/cancel/', {}, format='json')
        self.assertEqual(edit.status_code, status.HTTP_200_OK)
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.assertEqual(cancelled.data['status'], ScheduledPost.Status.CANCELLED)
        self.assertEqual(cancelled.data['targets'][0]['status'], ScheduledPostTarget.Status.CANCELLED)
        self.assertEqual(self.client.patch(f'/api/publishing/posts/{post_id}/', {'caption': 'Too late'}, format='json').status_code, status.HTTP_400_BAD_REQUEST)

    def test_post_with_a_completed_attempt_cannot_be_edited(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post = ScheduledPost.objects.get(pk=created.data['id'])
        target = post.targets.get()
        PublishingAttempt.objects.create(
            target=target,
            attempt_number=1,
            idempotency_key=f'{target.idempotency_key}:completed',
            status=PublishingAttempt.Status.FAILED,
            error='Completed attempt in this fixture.',
            finished_at=timezone.now(),
        )

        response = self.client.patch(f'/api/publishing/posts/{post.pk}/', {'caption': 'Changed'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        post.refresh_from_db()
        self.assertEqual(post.caption, 'A post caption')

    def test_other_user_cannot_read_edit_or_cancel_scheduled_posts(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        self.client.force_authenticate(self.other_user)

        self.assertEqual(self.client.get(f"/api/publishing/posts/{created.data['id']}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.patch(f"/api/publishing/posts/{created.data['id']}/", {'caption': 'x'}, format='json').status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post(f"/api/publishing/posts/{created.data['id']}/cancel/", {}, format='json').status_code, status.HTTP_404_NOT_FOUND)

    def test_content_and_publishing_apis_require_authentication(self):
        self.client.force_authenticate(user=None)

        responses = [
            self.client.get('/api/library/media/'),
            self.client.get('/api/publishing/posts/'),
            self.client.get('/api/publishing/history/'),
            self.client.get('/api/dashboard/summary/'),
        ]
        self.assertTrue(all(response.status_code == status.HTTP_403_FORBIDDEN for response in responses))

    @patch('library.tasks.process_scheduled_post.apply_async')
    def test_due_dispatcher_claims_a_post_and_does_not_queue_it_twice(self, enqueue):
        media = self.make_media()
        page = self.make_page()
        post = ScheduledPost.objects.create(
            owner=self.user,
            media=media,
            media_title_snapshot=media.title,
            scheduled_for=timezone.now() - timedelta(seconds=1),
        )
        ScheduledPostTarget.objects.create(post=post, page=page, page_id_snapshot=page.page_id, page_name_snapshot=page.name)

        with self.captureOnCommitCallbacks(execute=True):
            first = dispatch_due_scheduled_posts.run()
        second = dispatch_due_scheduled_posts.run()
        post.refresh_from_db()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(post.status, ScheduledPost.Status.QUEUED)
        enqueue.assert_called_once_with(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')

    def test_disabled_publishing_keeps_due_post_pending_without_attempt(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        with override_settings(PUBLISHING_ENABLED=False, PUBLISHING_DISPATCH_ENABLED=False):
            self.assertEqual(dispatch_due_scheduled_posts.run(), 0)
            process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')
        post.refresh_from_db()
        target = post.targets.get()
        self.assertEqual(post.status, ScheduledPost.Status.PENDING)
        self.assertEqual(target.status, ScheduledPostTarget.Status.PENDING)
        self.assertEqual(target.attempts.count(), 0)
        self.assertFalse(target.external_post_id)
        self.assertIsNone(target.published_at)
        self.assertIsNone(post.published_at)

    @patch('library.tasks.FacebookPublishingService.publish')
    def test_each_page_target_keeps_its_own_publishing_result(self, publish):
        from .services import PermanentPublishingError

        media = self.make_media()
        first_page = self.make_page()
        pages = [first_page, self.make_page(account=first_page.account)]
        created = self.schedule(media=media, pages=pages)
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        dispatch_due_scheduled_posts.run()
        publish.side_effect = [
            {'confirmed': True, 'external_post_id': 'meta-post-confirmed-by-test'},
            PermanentPublishingError('This Page is not permitted.'),
        ]

        process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')

        post.refresh_from_db()
        targets = list(post.targets.order_by('id'))
        self.assertEqual(post.status, ScheduledPost.Status.PARTIAL)
        self.assertEqual([target.status for target in targets], [ScheduledPostTarget.Status.PUBLISHED, ScheduledPostTarget.Status.FAILED])
        self.assertEqual(targets[0].external_post_id, 'meta-post-confirmed-by-test')
        self.assertFalse(targets[1].external_post_id)
        self.assertEqual([target.attempts.count() for target in targets], [1, 1])

    def test_meta_video_adapter_uses_chunked_upload_and_requires_confirmed_id(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        target = ScheduledPost.objects.get(pk=created.data['id']).targets.get()
        calls = []
        adapter = FacebookPublishingService()
        adapter.CHUNK_SIZE = 8
        self.publisher_safety.stop()

        def fake_post(endpoint, fields, file_field=None, file_chunk=None, offset=None):
            calls.append((fields.copy(), file_field, file_chunk))
            if fields.get('upload_phase') == 'start':
                return {'upload_session_id': 'session-1', 'video_id': 'video-1', 'start_offset': '0', 'end_offset': str(media.file_size)}
            if fields.get('upload_phase') == 'transfer':
                next_offset = int(fields['start_offset']) + len(file_chunk)
                return {'start_offset': str(next_offset), 'end_offset': str(media.file_size)}
            return {'success': True, 'video_id': 'video-1'}

        with patch.object(adapter, '_post', side_effect=fake_post):
            result = adapter.publish(target, str(target.idempotency_key))

        self.assertEqual(result, {'confirmed': True, 'external_post_id': 'video-1'})
        self.assertEqual(calls[0][0]['upload_phase'], 'start')
        self.assertGreater(sum(call[1] == 'video_file_chunk' for call in calls), 1)
        self.assertEqual(calls[-1][0]['upload_phase'], 'finish')
        self.assertIn('A post caption', calls[-1][0]['description'])

    def test_worker_records_uncertain_meta_finish_as_review_required(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        dispatch_due_scheduled_posts.run()

        with patch('library.tasks.FacebookPublishingService.publish', side_effect=UncertainPublishingError('Review the Page before retrying.')):
            process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')

        post.refresh_from_db()
        target = post.targets.get()
        self.assertEqual(post.status, ScheduledPost.Status.REVIEW_REQUIRED)
        self.assertEqual(target.status, ScheduledPostTarget.Status.REVIEW_REQUIRED)
        self.assertEqual(target.attempts.get().status, PublishingAttempt.Status.UNCERTAIN)
        self.assertFalse(target.external_post_id)

    def test_stale_processing_target_recovers_to_manual_review(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post = ScheduledPost.objects.get(pk=created.data['id'])
        target = post.targets.get()
        post.status = ScheduledPost.Status.PROCESSING
        post.save(update_fields=['status'])
        target.status = ScheduledPostTarget.Status.PROCESSING
        target.save(update_fields=['status'])
        attempt = PublishingAttempt.objects.create(
            target=target, attempt_number=1, idempotency_key=f'{target.idempotency_key}:stale',
        )
        stale_time = timezone.now() - timedelta(seconds=4000)
        ScheduledPostTarget.objects.filter(pk=target.pk).update(updated_at=stale_time)

        self.assertEqual(recover_stale_processing_targets.run(), 1)
        target.refresh_from_db(); post.refresh_from_db(); attempt.refresh_from_db()
        self.assertEqual(target.status, ScheduledPostTarget.Status.REVIEW_REQUIRED)
        self.assertEqual(post.status, ScheduledPost.Status.REVIEW_REQUIRED)
        self.assertEqual(attempt.status, PublishingAttempt.Status.UNCERTAIN)

    def test_worker_revalidation_requires_manage_posts_and_create_content(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        target = ScheduledPost.objects.get(pk=created.data['id']).targets.get()
        target.page.account.granted_permissions = ['pages_show_list', 'pages_read_engagement']
        target.page.account.save(update_fields=['granted_permissions'])
        self.assertIn('pages_manage_posts', validate_target_is_still_authorized(target))
        target.page.account.granted_permissions.append('pages_manage_posts')
        target.page.account.save(update_fields=['granted_permissions'])
        target.page.tasks = ['ANALYZE']
        target.page.save(update_fields=['tasks'])
        self.assertIn('CREATE_CONTENT', validate_target_is_still_authorized(target))

    def test_expired_page_token_is_reported_before_upload(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        target = ScheduledPost.objects.get(pk=created.data['id']).targets.get()
        with patch('library.services.graph_get', side_effect=FacebookTokenExpired('private token details')):
            message = validate_target_is_still_authorized(target)
        self.assertIn('token expired', message.lower())
        self.assertNotIn('private token details', message)

    def test_invalid_meta_upload_session_is_permanent_failure(self):
        from .services import PermanentPublishingError

        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        target = ScheduledPost.objects.get(pk=created.data['id']).targets.get()
        adapter = FacebookPublishingService()
        self.publisher_safety.stop()
        with patch.object(adapter, '_post', return_value={'unexpected': 'response'}):
            with self.assertRaises(PermanentPublishingError):
                adapter.publish(target, str(target.idempotency_key))

    def test_meta_rate_limit_is_classified_as_temporary_without_leaking_response(self):
        from .services import TemporaryPublishingError

        adapter = FacebookPublishingService()
        body = BytesIO(b'{"error":{"code":613,"message":"private-token-value"}}')
        error = HTTPError('https://graph-video.facebook.com', 429, 'rate limit', {}, body)
        with patch('library.services.urlopen', side_effect=error):
            with self.assertRaises(TemporaryPublishingError) as caught:
                adapter._post('page/videos', {'access_token': 'private-token-value'})
        self.assertNotIn('private-token-value', str(caught.exception))

    def test_temporary_network_failure_does_not_include_token(self):
        from .services import TemporaryPublishingError

        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        target = ScheduledPost.objects.get(pk=created.data['id']).targets.get()
        adapter = FacebookPublishingService()
        self.publisher_safety.stop()
        with patch.object(adapter, '_post', side_effect=URLError('private-token-value')):
            with self.assertRaises(TemporaryPublishingError) as caught:
                adapter.publish(target, str(target.idempotency_key))
        self.assertNotIn('private-token-value', str(caught.exception))

    def test_duplicate_worker_delivery_does_not_publish_twice(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        dispatch_due_scheduled_posts.run()
        with patch('library.tasks.FacebookPublishingService.publish', return_value={
            'confirmed': True, 'external_post_id': 'mock-video-1',
        }) as publish:
            process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')
            process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')
        self.assertEqual(publish.call_count, 1)
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledPost.Status.PUBLISHED)

    @patch('library.tasks.FacebookPublishingService.publish', side_effect=TemporaryPublishingError('Temporary Meta service error.'))
    def test_temporary_failure_is_recorded_and_retried_with_task_backoff(self, publish):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        dispatch_due_scheduled_posts.run()
        with self.assertRaises(Retry) as retry_signal:
            process_scheduled_post.run(post.pk)
        self.assertIsNotNone(retry_signal.exception)
        target = post.targets.get()
        self.assertEqual(target.status, ScheduledPostTarget.Status.PENDING)
        self.assertEqual(target.retry_count, 1)
        self.assertEqual(target.attempts.get().status, PublishingAttempt.Status.RETRYING)

        process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}', retries=1)
        target.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual(target.status, ScheduledPostTarget.Status.FAILED)
        self.assertEqual(target.attempts.count(), 2)
        self.assertEqual(post.status, ScheduledPost.Status.FAILED)
        self.assertEqual(publish.call_count, 2)

    def test_history_endpoint_only_returns_the_current_users_attempts(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        post = ScheduledPost.objects.get(pk=created.data['id'])
        post.scheduled_for = timezone.now() - timedelta(seconds=1)
        post.save(update_fields=['scheduled_for'])
        dispatch_due_scheduled_posts.run()
        process_scheduled_post.apply(args=(post.pk,), task_id=f'scheduled-post-{post.pk}')

        history = self.client.get('/api/publishing/history/')
        self.client.force_authenticate(self.other_user)
        other_history = self.client.get('/api/publishing/history/')

        self.assertEqual(history.status_code, status.HTTP_200_OK)
        self.assertEqual(len(history.data), 1)
        self.assertEqual(other_history.data, [])

    def test_dashboard_summary_counts_only_the_current_users_data(self):
        self.make_media()
        self.make_page()
        self.upload(user=self.other_user)

        summary = self.client.get('/api/dashboard/summary/')
        self.assertEqual(summary.status_code, status.HTTP_200_OK)
        self.assertEqual(summary.data['media_items'], 1)
        self.assertEqual(summary.data['facebook_pages'], 1)
        self.assertEqual(summary.data['scheduled_posts'], 0)

    def test_changing_rights_or_removing_media_cancels_pending_schedule(self):
        media = self.make_media()
        page = self.make_page()
        created = self.schedule(media=media, pages=[page])
        post = ScheduledPost.objects.get(pk=created.data['id'])

        self.client.patch(f'/api/library/media/{media.pk}/rights/', {'rights_status': 'pending_review'}, format='json')
        post.refresh_from_db()
        self.assertEqual(post.status, ScheduledPost.Status.CANCELLED)
        self.assertEqual(post.targets.get().status, ScheduledPostTarget.Status.CANCELLED)

        media2 = self.make_media()
        created2 = self.schedule(media=media2, pages=[page])
        post2 = ScheduledPost.objects.get(pk=created2.data['id'])
        file_name = media2.file.name
        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.delete(f'/api/library/media/{media2.pk}/')
        post2.refresh_from_db()
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(post2.status, ScheduledPost.Status.CANCELLED)
        self.assertIsNone(post2.media)
        self.assertFalse(media2.file.storage.exists(file_name))
