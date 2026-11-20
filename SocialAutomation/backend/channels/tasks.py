"""Background tasks for periodic, owner-independent public metadata checks."""
import logging

from celery import shared_task
from django.conf import settings

from .models import Channel
from .services import check_channel
from .youtube_api import RetryableYouTubeAPIError, YouTubeAPIError

logger = logging.getLogger(__name__)


@shared_task(ignore_result=True)
def enqueue_enabled_channel_checks():
    """Queue only channels whose monitoring preference is currently enabled."""
    if not settings.YOUTUBE_MONITORING_ENABLED:
        return 0
    channel_ids = Channel.objects.filter(monitoring_enabled=True).values_list("id", flat=True)
    queued = 0
    for channel_id in channel_ids.iterator():
        monitor_channel.apply_async(
            args=(channel_id,),
            expires=settings.YOUTUBE_MONITOR_INTERVAL_SECONDS,
        )
        queued += 1
    return queued


@shared_task(
    autoretry_for=(RetryableYouTubeAPIError,),
    retry_backoff=60,
    retry_backoff_max=3600,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def monitor_channel(channel_id):
    """Check a single channel; temporary API or quota errors are retried with backoff."""
    if not settings.YOUTUBE_MONITORING_ENABLED:
        return {"status": "skipped", "reason": "monitoring_service_disabled"}
    try:
        result = check_channel(channel_id)
    except RetryableYouTubeAPIError:
        logger.warning("Temporary YouTube API error while monitoring channel %s; retrying", channel_id)
        raise
    except YouTubeAPIError as error:
        logger.warning("YouTube channel %s could not be monitored: %s", channel_id, error)
        return {"status": "error", "channel_id": channel_id, "detail": str(error)}

    if result["status"] == "checked":
        logger.info(
            "Monitored YouTube channel %s and saved %s video records",
            channel_id,
            result["videos_saved"],
        )
    return result
