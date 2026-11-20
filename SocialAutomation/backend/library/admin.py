from django.contrib import admin

from .models import MediaAsset, PublishingAttempt, ScheduledPost, ScheduledPostTarget


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'processing_status', 'rights_status', 'created_at')
    list_filter = ('processing_status', 'rights_status')
    search_fields = ('title', 'owner__username')
    readonly_fields = ('file', 'detected_content_type', 'file_size', 'approved_at', 'approved_by', 'created_at', 'updated_at')


class ScheduledPostTargetInline(admin.TabularInline):
    model = ScheduledPostTarget
    extra = 0
    readonly_fields = ('page_id_snapshot', 'page_name_snapshot', 'status', 'retry_count', 'last_error', 'external_post_id', 'published_at')
    can_delete = False


@admin.register(ScheduledPost)
class ScheduledPostAdmin(admin.ModelAdmin):
    list_display = ('media_title_snapshot', 'owner', 'scheduled_for', 'status', 'retry_count')
    list_filter = ('status',)
    search_fields = ('media_title_snapshot', 'owner__username')
    readonly_fields = ('owner', 'media', 'media_title_snapshot', 'status', 'idempotency_key', 'queue_claimed_at', 'processing_task_id', 'retry_count', 'last_error', 'published_at', 'created_at', 'updated_at')
    inlines = [ScheduledPostTargetInline]


@admin.register(PublishingAttempt)
class PublishingAttemptAdmin(admin.ModelAdmin):
    list_display = ('target', 'attempt_number', 'status', 'started_at', 'finished_at')
    list_filter = ('status',)
    readonly_fields = ('target', 'attempt_number', 'idempotency_key', 'status', 'error', 'external_post_id', 'started_at', 'finished_at')
