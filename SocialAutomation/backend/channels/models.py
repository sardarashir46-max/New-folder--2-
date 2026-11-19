from django.conf import settings
from django.db import models


class Channel(models.Model):
    class ConnectionStatus(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="youtube_channels")
    channel_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    custom_url = models.CharField(max_length=255, blank=True)
    thumbnail_url = models.URLField(max_length=1000, blank=True)
    subscriber_count = models.PositiveBigIntegerField(default=0)
    video_count = models.PositiveBigIntegerField(default=0)
    view_count = models.PositiveBigIntegerField(default=0)
    connection_status = models.CharField(max_length=20, choices=ConnectionStatus.choices, default=ConnectionStatus.DISCONNECTED)
    monitoring_enabled = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["owner", "channel_id"], name="unique_channel_per_owner")]

    def __str__(self):
        return self.title


class DiscoveredVideo(models.Model):
    class RightsStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        OWNED = "owned", "Owned by me"
        AUTHORIZED = "authorized", "Redistribution authorized"
        REJECTED = "rejected", "Not approved"

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="videos")
    video_id = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    thumbnail_url = models.URLField(max_length=1000, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    video_url = models.URLField(max_length=500)
    rights_status = models.CharField(max_length=20, choices=RightsStatus.choices, default=RightsStatus.UNVERIFIED)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-discovered_at"]

    def __str__(self):
        return self.title
