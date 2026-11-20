"""Shared metadata discovery logic used by manual refreshes and monitoring."""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from .models import DiscoveredVideo
from .youtube_api import get_channel, get_latest_videos


def save_discovered_videos(channel, video_items):
    """Insert or update public video metadata without changing rights declarations."""
    saved = 0
    with transaction.atomic():
        for item in video_items:
            published_at = item.get("published_at")
            if published_at:
                published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            DiscoveredVideo.objects.update_or_create(
                channel=channel,
                video_id=item["video_id"],
                defaults={
                    "title": item["title"],
                    "description": item.get("description", ""),
                    "thumbnail_url": item.get("thumbnail_url", ""),
                    "published_at": published_at,
                    "video_url": item["video_url"],
                },
            )
            saved += 1
    return saved


def check_channel(channel_id):
    """Fetch one enabled channel and commit metadata only after both API calls succeed."""
    from .models import Channel

    channel = Channel.objects.filter(pk=channel_id, monitoring_enabled=True).first()
    if channel is None:
        return {"status": "skipped", "reason": "monitoring_disabled_or_channel_removed"}

    info = get_channel(channel.channel_id)
    uploads_playlist_id = info.get("uploads_playlist_id")
    video_items = get_latest_videos(uploads_playlist_id)

    # A user can pause monitoring while the YouTube request is in flight.
    # Check once more before saving any results from that queued task.
    with transaction.atomic():
        channel = Channel.objects.select_for_update().filter(
            pk=channel_id,
            monitoring_enabled=True,
        ).first()
        if channel is None:
            return {"status": "skipped", "reason": "monitoring_disabled_or_channel_removed"}

        for field in (
            "title",
            "description",
            "custom_url",
            "thumbnail_url",
            "subscriber_count",
            "video_count",
            "view_count",
        ):
            if field in info:
                setattr(channel, field, info[field])

        saved_count = save_discovered_videos(channel, video_items)
        channel.last_checked_at = timezone.now()
        channel.save(update_fields=[
            "title", "description", "custom_url", "thumbnail_url",
            "subscriber_count", "video_count", "view_count",
            "last_checked_at", "updated_at",
        ])

    return {"status": "checked", "channel_id": channel_id, "videos_saved": saved_count}
