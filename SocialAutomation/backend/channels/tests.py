from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Channel, DiscoveredVideo
from .youtube_api import YouTubeAPIError, _channel_lookup


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
