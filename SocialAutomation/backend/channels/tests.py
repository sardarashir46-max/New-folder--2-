import io
import json
from urllib.error import HTTPError
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Channel, DiscoveredVideo
from .services import check_channel
from .tasks import enqueue_enabled_channel_checks, monitor_channel
from .youtube_api import RetryableYouTubeAPIError, YouTubeAPIError, _api_get, _channel_lookup


class ChannelAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="owner", password="pass")
        self.other_user = user_model.objects.create_user(username="other", password="pass")
        self.channel = Channel.objects.create(owner=self.owner, channel_id="UC-example", title="Example channel")
        self.list_url = "/api/channels/"

    def test_channel_lookup_accepts_handles_and_official_youtube_hosts(self):
        self.assertEqual(_channel_lookup("@sample"), {"forHandle": "@sample"})
        self.assertEqual(_channel_lookup("https://www.youtube.com/@sample"), {"forHandle": "@sample"})
        self.assertEqual(_channel_lookup("https://m.youtube.com/@sample"), {"forHandle": "@sample"})

    def test_channel_lookup_rejects_lookalike_hosts(self):
        for value in (
            "https://evil-youtube.com/@sample",
            "https://youtube.com.attacker.example/@sample",
        ):
            with self.subTest(value=value):
                with self.assertRaises(YouTubeAPIError):
                    _channel_lookup(value)

    def test_authentication_is_required(self):
        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_403_FORBIDDEN)

    def test_users_only_see_their_own_channels(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_create_sets_owner_and_does_not_allow_fake_connection(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.list_url, {"channel_id": "UC-new", "title": "New channel", "owner": self.other_user.pk, "connection_status": "connected"}, format="json")
        # Channel creation must go through YouTube metadata verification.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Channel.objects.filter(channel_id="UC-new").exists())

    def test_other_user_cannot_read_or_delete_a_channel(self):
        self.client.force_authenticate(self.other_user)
        detail_url = f"{self.list_url}{self.channel.pk}/"
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.patch(detail_url, {"title": "Stolen"}, format="json").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.title, "Example channel")

    def test_owner_can_update_and_delete_monitoring_channel(self):
        self.client.force_authenticate(self.owner)
        detail_url = f"{self.list_url}{self.channel.pk}/"
        response = self.client.patch(detail_url, {"monitoring_enabled": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.monitoring_enabled)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_204_NO_CONTENT)

    def test_channel_add_uses_youtube_metadata_and_discovers_videos(self):
        self.client.force_authenticate(self.owner)
        info = {"channel_id": "UC" + "a" * 22, "title": "Test channel", "description": "About", "custom_url": "@test", "thumbnail_url": "https://example.com/thumb.jpg", "subscriber_count": 12, "video_count": 1, "view_count": 99, "uploads_playlist_id": "UU-test"}
        video = {"video_id": "video1234567", "title": "Latest video", "description": "Description", "thumbnail_url": "https://example.com/video.jpg", "published_at": "2026-11-19T12:00:00Z", "video_url": "https://www.youtube.com/watch?v=video1234567"}
        with patch("channels.views.get_channel", return_value=info), patch("channels.views.get_latest_videos", return_value=[video]):
            response = self.client.post(self.list_url, {"channel_url": "https://www.youtube.com/@test"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Test channel")
        self.assertEqual(DiscoveredVideo.objects.filter(video_id="video1234567").count(), 1)
        self.assertEqual(DiscoveredVideo.objects.get(video_id="video1234567").rights_status, "unverified")

    def test_duplicate_channel_does_not_create_second_record(self):
        self.client.force_authenticate(self.owner)
        info = {"channel_id": self.channel.channel_id, "title": "Example channel", "description": "", "custom_url": "", "thumbnail_url": "", "subscriber_count": 0, "video_count": 0, "view_count": 0, "uploads_playlist_id": ""}
        with patch("channels.views.get_channel", return_value=info):
            first = self.client.post(self.list_url, {"channel_url": self.channel.channel_id}, format="json")
            second = self.client.post(self.list_url, {"channel_url": self.channel.channel_id}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Channel.objects.filter(owner=self.owner, channel_id=self.channel.channel_id).count(), 1)

    def test_refresh_is_owner_scoped(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.post(f"{self.list_url}{self.channel.pk}/refresh/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_monitoring_toggle(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"{self.list_url}{self.channel.pk}/monitoring/", {"enabled": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["monitoring_enabled"])

    def test_rights_status_cannot_be_set_to_unverified(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(f"{self.list_url}{self.channel.pk}/videos/", {"video_id": "none", "rights_status": "unverified"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_record_a_rights_attestation(self):
        video = DiscoveredVideo.objects.create(
            channel=self.channel,
            video_id="rights123456",
            title="Rights review test",
            video_url="https://www.youtube.com/watch?v=rights123456",
        )
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"{self.list_url}{self.channel.pk}/videos/",
            {"video_id": video.video_id, "rights_status": "authorized"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        video.refresh_from_db()
        self.assertEqual(video.rights_status, "authorized")

    def test_discovered_videos_are_owner_scoped(self):
        video = DiscoveredVideo.objects.create(channel=self.channel, video_id="v1234567890", title="Test", video_url="https://youtube.com/watch?v=v1234567890")
        self.client.force_authenticate(self.other_user)
        response = self.client.get(f"{self.list_url}{self.channel.pk}/videos/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(DiscoveredVideo.objects.filter(pk=video.pk).exists())

    def test_same_video_can_be_discovered_for_two_different_channels(self):
        other_channel = Channel.objects.create(
            owner=self.other_user,
            channel_id="UC-other",
            title="Other channel",
        )
        first = DiscoveredVideo.objects.create(
            channel=self.channel,
            video_id="sharedvideo01",
            title="Shared public video",
            video_url="https://www.youtube.com/watch?v=sharedvideo01",
        )
        second = DiscoveredVideo.objects.create(
            channel=other_channel,
            video_id="sharedvideo01",
            title="Shared public video",
            video_url="https://www.youtube.com/watch?v=sharedvideo01",
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            DiscoveredVideo.objects.filter(video_id="sharedvideo01").count(),
            2,
        )

    def test_monitoring_summary_is_scoped_to_signed_in_user(self):
        self.channel.last_checked_at = timezone.now()
        self.channel.save(update_fields=["last_checked_at"])
        DiscoveredVideo.objects.create(
            channel=self.channel,
            video_id="ownvideo12345",
            title="My discovered video",
            video_url="https://www.youtube.com/watch?v=ownvideo12345",
        )
        other_channel = Channel.objects.create(
            owner=self.other_user,
            channel_id="UC-other",
            title="Other user's channel",
        )
        DiscoveredVideo.objects.create(
            channel=other_channel,
            video_id="othervideo12345",
            title="Other user's video",
            video_url="https://www.youtube.com/watch?v=othervideo12345",
        )

        self.client.force_authenticate(self.owner)
        response = self.client.get(f"{self.list_url}monitoring-summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_channels"], 1)
        self.assertEqual(response.data["active_monitoring_channels"], 1)
        self.assertEqual(response.data["total_discovered_videos"], 1)
        self.assertEqual(response.data["latest_videos"][0]["title"], "My discovered video")

    def test_monitoring_summary_reports_global_service_disabled(self):
        self.client.force_authenticate(self.owner)
        with override_settings(YOUTUBE_MONITORING_ENABLED=False):
            response = self.client.get(f"{self.list_url}monitoring-summary/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["monitoring_service_enabled"])
        self.assertEqual(response.data["active_monitoring_channels"], 0)


class ChannelMonitoringTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="monitor-owner",
            password="pass",
        )
        self.enabled_channel = Channel.objects.create(
            owner=self.user,
            channel_id="UC-enabled",
            title="Enabled channel",
        )
        self.disabled_channel = Channel.objects.create(
            owner=self.user,
            channel_id="UC-disabled",
            title="Disabled channel",
            monitoring_enabled=False,
        )

    @patch("channels.tasks.monitor_channel.apply_async")
    def test_dispatcher_queues_enabled_channels_only(self, apply_async):
        with override_settings(YOUTUBE_MONITOR_INTERVAL_SECONDS=300):
            enqueue_enabled_channel_checks.run()

        apply_async.assert_called_once_with(args=(self.enabled_channel.pk,), expires=300)

    @patch("channels.tasks.monitor_channel.apply_async")
    def test_global_setting_can_disable_dispatch(self, apply_async):
        with override_settings(YOUTUBE_MONITORING_ENABLED=False):
            queued = enqueue_enabled_channel_checks.run()

        self.assertEqual(queued, 0)
        apply_async.assert_not_called()

    @patch("channels.tasks.check_channel")
    def test_global_setting_can_disable_queued_channel_task(self, check_channel_mock):
        with override_settings(YOUTUBE_MONITORING_ENABLED=False):
            result = monitor_channel.run(self.enabled_channel.pk)

        self.assertEqual(result["reason"], "monitoring_service_disabled")
        check_channel_mock.assert_not_called()

    @patch("channels.services.get_latest_videos")
    @patch("channels.services.get_channel")
    def test_check_saves_metadata_once_and_updates_last_success(self, get_channel, get_latest_videos):
        get_channel.return_value = {
            "channel_id": self.enabled_channel.channel_id,
            "title": "Updated title",
            "description": "Updated description",
            "custom_url": "@enabled",
            "thumbnail_url": "https://example.com/channel.jpg",
            "subscriber_count": 8,
            "video_count": 1,
            "view_count": 24,
            "uploads_playlist_id": "uploads-enabled",
        }
        get_latest_videos.return_value = [{
            "video_id": "monitorvideo123",
            "title": "New public upload",
            "description": "Video metadata",
            "thumbnail_url": "https://example.com/video.jpg",
            "published_at": "2026-11-20T10:30:00Z",
            "video_url": "https://www.youtube.com/watch?v=monitorvideo123",
        }]

        result = check_channel(self.enabled_channel.pk)
        video = DiscoveredVideo.objects.get(channel=self.enabled_channel)
        video.rights_status = DiscoveredVideo.RightsStatus.AUTHORIZED
        video.save(update_fields=["rights_status"])
        check_channel(self.enabled_channel.pk)

        self.enabled_channel.refresh_from_db()
        video.refresh_from_db()
        self.assertEqual(result["status"], "checked")
        self.assertEqual(result["videos_saved"], 1)
        self.assertEqual(self.enabled_channel.title, "Updated title")
        self.assertIsNotNone(self.enabled_channel.last_checked_at)
        self.assertEqual(DiscoveredVideo.objects.filter(channel=self.enabled_channel).count(), 1)
        self.assertEqual(video.rights_status, DiscoveredVideo.RightsStatus.AUTHORIZED)
        self.assertEqual(get_latest_videos.call_count, 2)

    @patch("channels.services.get_channel")
    def test_disabled_channel_is_skipped_without_youtube_request(self, get_channel):
        result = check_channel(self.disabled_channel.pk)

        self.assertEqual(result["status"], "skipped")
        get_channel.assert_not_called()
        self.assertIsNone(self.disabled_channel.last_checked_at)

    @patch("channels.tasks.check_channel", side_effect=RetryableYouTubeAPIError("temporary"))
    @patch.object(monitor_channel, "retry", side_effect=Exception("retry requested"))
    def test_temporary_api_errors_trigger_celery_retry(self, retry, check):
        with self.assertRaisesRegex(Exception, "retry requested"):
            monitor_channel.run(self.enabled_channel.pk)

        retry.assert_called_once()
        self.assertEqual(monitor_channel.autoretry_for, (RetryableYouTubeAPIError,))
        self.assertTrue(monitor_channel.retry_backoff)
        self.assertEqual(check.call_count, 1)

    @patch("channels.tasks.check_channel", side_effect=YouTubeAPIError("bad API key"))
    @patch.object(monitor_channel, "retry")
    def test_permanent_api_errors_are_not_retried(self, retry, check):
        result = monitor_channel.run(self.enabled_channel.pk)

        self.assertEqual(result["status"], "error")
        retry.assert_not_called()
        check.assert_called_once()

    @patch("channels.services.get_latest_videos", side_effect=RetryableYouTubeAPIError("temporarily unavailable"))
    @patch("channels.services.get_channel")
    def test_failed_check_does_not_advance_last_success_time(self, get_channel, get_latest_videos):
        get_channel.return_value = {
            "channel_id": self.enabled_channel.channel_id,
            "title": self.enabled_channel.title,
            "uploads_playlist_id": "uploads-enabled",
        }

        with self.assertRaises(RetryableYouTubeAPIError):
            check_channel(self.enabled_channel.pk)

        self.enabled_channel.refresh_from_db()
        self.assertIsNone(self.enabled_channel.last_checked_at)
        self.assertFalse(DiscoveredVideo.objects.filter(channel=self.enabled_channel).exists())


class YouTubeAPIErrorClassificationTests(SimpleTestCase):
    @patch("channels.youtube_api.urlopen")
    def test_quota_error_is_classified_for_retry(self, urlopen):
        body = json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}}).encode()
        urlopen.side_effect = HTTPError(
            "https://www.googleapis.com/youtube/v3/channels",
            403,
            "Forbidden",
            {},
            io.BytesIO(body),
        )
        with patch.dict("os.environ", {"YOUTUBE_API_KEY": "test-key"}):
            with self.assertRaises(RetryableYouTubeAPIError):
                _api_get("channels", {"part": "snippet"})

    @patch("channels.youtube_api.urlopen")
    def test_permanent_permission_error_is_not_classified_for_retry(self, urlopen):
        body = json.dumps({"error": {"errors": [{"reason": "forbidden"}]}}).encode()
        urlopen.side_effect = HTTPError(
            "https://www.googleapis.com/youtube/v3/channels",
            403,
            "Forbidden",
            {},
            io.BytesIO(body),
        )
        with patch.dict("os.environ", {"YOUTUBE_API_KEY": "test-key"}):
            with self.assertRaises(YouTubeAPIError) as error:
                _api_get("channels", {"part": "snippet"})
        self.assertNotIsInstance(error.exception, RetryableYouTubeAPIError)
