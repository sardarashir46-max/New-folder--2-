from datetime import datetime
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Channel, DiscoveredVideo
from .serializers import ChannelSerializer, DiscoveredVideoSerializer
from .youtube_api import YouTubeAPIError, get_channel, get_latest_videos


class ChannelViewSet(viewsets.ModelViewSet):
    serializer_class = ChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Channel.objects.filter(owner=self.request.user).prefetch_related("videos")

    def create(self, request, *args, **kwargs):
        channel_url = request.data.get("channel_url") or request.data.get("channel_id", "")
        try:
            info = get_channel(channel_url)
        except YouTubeAPIError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        channel, created = Channel.objects.get_or_create(
            owner=request.user,
            channel_id=info["channel_id"],
            defaults={key: value for key, value in info.items() if key != "uploads_playlist_id"},
        )
        if not created:
            for key, value in info.items():
                if key != "uploads_playlist_id":
                    setattr(channel, key, value)
            channel.save()
        self._discover(channel, info.get("uploads_playlist_id"))
        return Response(self.get_serializer(channel).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def _discover(self, channel, uploads_playlist_id=None):
        if not uploads_playlist_id:
            # Retrieve the uploads playlist from the channel resource again.
            try:
                info = get_channel(channel.channel_id)
                uploads_playlist_id = info.get("uploads_playlist_id")
            except YouTubeAPIError:
                return
        try:
            for item in get_latest_videos(uploads_playlist_id):
                published_at = item.get("published_at")
                if published_at:
                    published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                DiscoveredVideo.objects.update_or_create(
                    video_id=item["video_id"],
                    defaults={
                        "channel": channel,
                        "title": item["title"],
                        "description": item["description"],
                        "thumbnail_url": item["thumbnail_url"],
                        "published_at": published_at,
                        "video_url": item["video_url"],
                    },
                )
            channel.last_checked_at = timezone.now()
            channel.save(update_fields=["last_checked_at", "updated_at"])
        except YouTubeAPIError:
            # Keep the channel record; the UI can retry discovery later.
            return

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        channel = self.get_object()
        try:
            info = get_channel(channel.channel_id)
            for key, value in info.items():
                if key != "uploads_playlist_id":
                    setattr(channel, key, value)
            channel.save()
            self._discover(channel, info.get("uploads_playlist_id"))
        except YouTubeAPIError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        channel.refresh_from_db()
        return Response(self.get_serializer(channel).data)

    @action(detail=True, methods=["get", "patch"])
    def videos(self, request, pk=None):
        channel = self.get_object()
        if request.method == "PATCH":
            video_id = request.data.get("video_id")
            rights_status = request.data.get("rights_status")
            allowed = {choice for choice, _label in DiscoveredVideo.RightsStatus.choices}
            if rights_status not in allowed - {DiscoveredVideo.RightsStatus.UNVERIFIED}:
                return Response({"detail": "Choose an explicit rights status."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                video = channel.videos.get(video_id=video_id)
            except DiscoveredVideo.DoesNotExist:
                return Response({"detail": "Video not found."}, status=status.HTTP_404_NOT_FOUND)
            video.rights_status = rights_status
            video.save(update_fields=["rights_status"])
            return Response(DiscoveredVideoSerializer(video).data)
        videos = channel.videos.all()
        return Response(DiscoveredVideoSerializer(videos, many=True).data)

    @action(detail=True, methods=["post"])
    def monitoring(self, request, pk=None):
        channel = self.get_object()
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            return Response({"detail": "enabled must be true or false."}, status=status.HTTP_400_BAD_REQUEST)
        channel.monitoring_enabled = enabled
        channel.save(update_fields=["monitoring_enabled", "updated_at"])
        return Response(self.get_serializer(channel).data)
