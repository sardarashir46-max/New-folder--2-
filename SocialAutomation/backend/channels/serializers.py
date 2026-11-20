from rest_framework import serializers
from .models import Channel, DiscoveredVideo


class ChannelSerializer(serializers.ModelSerializer):
    channel_url = serializers.CharField(write_only=True, required=False, allow_blank=False)
    class Meta:
        model = Channel
        fields = ["id", "channel_id", "title", "description", "custom_url", "thumbnail_url", "subscriber_count", "video_count", "view_count", "connection_status", "monitoring_enabled", "last_checked_at", "created_at", "updated_at", "owner", "channel_url"]
        read_only_fields = ["id", "channel_id", "title", "description", "custom_url", "thumbnail_url", "subscriber_count", "video_count", "view_count", "connection_status", "last_checked_at", "created_at", "updated_at", "owner"]


class DiscoveredVideoSerializer(serializers.ModelSerializer):
    channel_title = serializers.CharField(source="channel.title", read_only=True)
    class Meta:
        model = DiscoveredVideo
        fields = ["id", "channel", "channel_title", "video_id", "title", "description", "thumbnail_url", "published_at", "video_url", "rights_status", "discovered_at"]
        read_only_fields = ["id", "channel", "channel_title", "video_id", "title", "description", "thumbnail_url", "published_at", "video_url", "discovered_at"]
