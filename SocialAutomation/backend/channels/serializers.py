from rest_framework import serializers

from .models import Channel


class ChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Channel
        fields = [
            "id",
            "channel_id",
            "title",
            "description",
            "custom_url",
            "thumbnail_url",
            "subscriber_count",
            "video_count",
            "view_count",
            "connection_status",
            "created_at",
            "updated_at",
            "owner",
        ]
        read_only_fields = [
            "id",
            "connection_status",
            "created_at",
            "updated_at",
            "owner",
        ]
