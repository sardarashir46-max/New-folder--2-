from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Channel


class ChannelAPITests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(username="owner", password="pass")
        self.other_user = user_model.objects.create_user(username="other", password="pass")
        self.channel = Channel.objects.create(
            owner=self.owner,
            channel_id="UC-example",
            title="Example channel",
        )
        self.list_url = "/api/channels/"

    def test_authentication_is_required(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_users_only_see_their_own_channels(self):
        self.client.force_authenticate(self.other_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_create_sets_owner_and_does_not_allow_fake_connection(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            self.list_url,
            {
                "channel_id": "UC-new",
                "title": "New channel",
                "owner": self.other_user.pk,
                "connection_status": "connected",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        channel = Channel.objects.get(channel_id="UC-new")
        self.assertEqual(channel.owner, self.owner)
        self.assertEqual(channel.connection_status, Channel.ConnectionStatus.DISCONNECTED)

    def test_other_user_cannot_read_or_delete_a_channel(self):
        self.client.force_authenticate(self.other_user)
        detail_url = f"{self.list_url}{self.channel.pk}/"
        self.assertEqual(self.client.get(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Channel.objects.filter(pk=self.channel.pk).exists())

    def test_owner_can_update_and_delete_a_channel(self):
        self.client.force_authenticate(self.owner)
        detail_url = f"{self.list_url}{self.channel.pk}/"
        response = self.client.put(
            detail_url,
            {"channel_id": "UC-example", "title": "Replaced"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        response = self.client.patch(detail_url, {"title": "Updated"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.title, "Updated")
        self.assertEqual(self.client.delete(detail_url).status_code, status.HTTP_204_NO_CONTENT)

    def test_response_contains_no_credentials(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(self.list_url)
        self.assertNotIn("access_token", response.data[0])
        self.assertNotIn("refresh_token", response.data[0])
