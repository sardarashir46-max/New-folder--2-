import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from facebook.models import FacebookPage

from .uploads import private_video_upload_path


class MediaAsset(models.Model):
    class ProcessingStatus(models.TextChoices):
        READY = 'ready', 'Ready'
        PROCESSING = 'processing', 'Processing'
        FAILED = 'failed', 'Processing failed'

    class RightsStatus(models.TextChoices):
        PENDING = 'pending_review', 'Pending review'
        OWNED = 'owned', 'Owned by me'
        AUTHORIZED = 'authorized', 'Authorized to redistribute'
        REJECTED = 'rejected', 'Rejected'

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='media_assets')
    file = models.FileField(upload_to=private_video_upload_path)
    detected_content_type = models.CharField(max_length=100)
    file_size = models.PositiveBigIntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    caption = models.TextField(blank=True)
    processing_status = models.CharField(max_length=16, choices=ProcessingStatus.choices, default=ProcessingStatus.READY)
    rights_status = models.CharField(max_length=20, choices=RightsStatus.choices, default=RightsStatus.PENDING)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name='approved_media_assets',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class ScheduledPost(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        QUEUED = 'queued', 'Queued'
        PROCESSING = 'processing', 'Processing'
        PUBLISHED = 'published', 'Published'
        PARTIAL = 'partial', 'Partially published'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='scheduled_posts')
    media = models.ForeignKey(MediaAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name='scheduled_posts')
    media_title_snapshot = models.CharField(max_length=255)
    caption = models.TextField(blank=True)
    description = models.TextField(blank=True)
    scheduled_for = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    queue_claimed_at = models.DateTimeField(null=True, blank=True)
    processing_task_id = models.CharField(max_length=255, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['scheduled_for', 'id']

    def __str__(self):
        return f'{self.media_title_snapshot} ({self.status})'


class ScheduledPostTarget(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        PUBLISHED = 'published', 'Published'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    post = models.ForeignKey(ScheduledPost, on_delete=models.CASCADE, related_name='targets')
    page = models.ForeignKey(FacebookPage, on_delete=models.SET_NULL, null=True, blank=True, related_name='scheduled_targets')
    page_id_snapshot = models.CharField(max_length=100)
    page_name_snapshot = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    retry_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    external_post_id = models.CharField(max_length=255, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['post', 'page'], name='unique_page_target_per_scheduled_post'),
        ]

    def __str__(self):
        return f'{self.post_id} → {self.page_name_snapshot}'


class PublishingAttempt(models.Model):
    class Status(models.TextChoices):
        PROCESSING = 'processing', 'Processing'
        RETRYING = 'retrying', 'Waiting to retry'
        PUBLISHED = 'published', 'Published'
        FAILED = 'failed', 'Failed'

    target = models.ForeignKey(ScheduledPostTarget, on_delete=models.CASCADE, related_name='attempts')
    attempt_number = models.PositiveIntegerField()
    idempotency_key = models.CharField(max_length=180, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROCESSING)
    error = models.TextField(blank=True)
    external_post_id = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['target', 'attempt_number'], name='unique_attempt_number_per_page_target'),
        ]

    def __str__(self):
        return f'Target {self.target_id}, attempt {self.attempt_number}: {self.status}'
