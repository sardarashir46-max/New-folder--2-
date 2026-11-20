import uuid

from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from facebook.models import FacebookAccount, FacebookPage
from facebook.oauth import FacebookIntegrationError
from facebook.services import usable_access_token

from .models import MediaAsset, PublishingAttempt, ScheduledPost, ScheduledPostTarget
from .uploads import validate_video_upload


class MediaAssetSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=True)
    file_url = serializers.SerializerMethodField()
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, default=None)

    class Meta:
        model = MediaAsset
        fields = [
            'id', 'file', 'file_url', 'detected_content_type', 'file_size', 'title',
            'description', 'caption', 'processing_status', 'rights_status',
            'approved_at', 'approved_by_username', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'file_url', 'detected_content_type', 'file_size', 'processing_status',
            'rights_status', 'approved_at', 'approved_by_username', 'created_at', 'updated_at',
        ]
        extra_kwargs = {'title': {'required': False}}

    def get_file_url(self, asset):
        return f'/api/library/media/{asset.pk}/file/'

    def validate_file(self, upload):
        if self.instance:
            raise serializers.ValidationError('Replace a video by uploading it as a new library item.')
        try:
            _extension, content_type = validate_video_upload(upload)
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.messages) from None
        self._detected_content_type = content_type
        return upload

    def create(self, validated_data):
        validated_data['detected_content_type'] = self._detected_content_type
        validated_data['file_size'] = validated_data['file'].size
        validated_data.setdefault('title', 'Uploaded video')
        return super().create(validated_data)


class MediaRightsSerializer(serializers.Serializer):
    rights_status = serializers.ChoiceField(choices=MediaAsset.RightsStatus.choices)


class ScheduledPostTargetSerializer(serializers.ModelSerializer):
    page_id = serializers.CharField(source='page_id_snapshot', read_only=True)
    page_name = serializers.CharField(source='page_name_snapshot', read_only=True)

    class Meta:
        model = ScheduledPostTarget
        fields = [
            'id', 'page_id', 'page_name', 'status', 'retry_count', 'last_error',
            'external_post_id', 'published_at',
        ]
        read_only_fields = fields


class ScheduledPostSerializer(serializers.ModelSerializer):
    media = serializers.PrimaryKeyRelatedField(queryset=MediaAsset.objects.all())
    media_title = serializers.CharField(source='media_title_snapshot', read_only=True)
    target_pages = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        required=True,
        allow_empty=False,
    )
    targets = ScheduledPostTargetSerializer(many=True, read_only=True)
    idempotency_key = serializers.UUIDField(write_only=True, required=True)

    class Meta:
        model = ScheduledPost
        fields = [
            'id', 'media', 'media_title', 'caption', 'description', 'scheduled_for',
            'status', 'target_pages', 'targets', 'retry_count', 'last_error',
            'published_at', 'created_at', 'updated_at', 'idempotency_key',
        ]
        read_only_fields = ['id', 'media_title', 'status', 'targets', 'retry_count', 'last_error', 'published_at', 'created_at', 'updated_at']

    def validate(self, attrs):
        request = self.context['request']
        media = attrs.get('media', self.instance.media if self.instance else None)
        if media is None or media.owner_id != request.user.pk:
            raise serializers.ValidationError({'media': 'Choose a video from your own library.'})
        if media.processing_status != MediaAsset.ProcessingStatus.READY:
            raise serializers.ValidationError({'media': 'This video is not ready for scheduling.'})
        if media.rights_status not in (MediaAsset.RightsStatus.OWNED, MediaAsset.RightsStatus.AUTHORIZED):
            raise serializers.ValidationError({'media': 'Only Owned or Authorized media can be scheduled.'})

        scheduled_for = attrs.get('scheduled_for', self.instance.scheduled_for if self.instance else None)
        if scheduled_for is None or scheduled_for <= timezone.now():
            raise serializers.ValidationError({'scheduled_for': 'Choose a future date and time.'})

        page_ids = attrs.get('target_pages')
        if page_ids is None and self.instance:
            page_ids = list(self.instance.targets.filter(page__isnull=False).values_list('page_id', flat=True))
        if page_ids is None:
            raise serializers.ValidationError({'target_pages': 'Select at least one Facebook Page.'})
        if len(set(page_ids)) != len(page_ids):
            raise serializers.ValidationError({'target_pages': 'A Facebook Page can only be selected once.'})

        pages = list(FacebookPage.objects.filter(
            pk__in=page_ids,
            account__owner=request.user,
            account__status=FacebookAccount.Status.CONNECTED,
            available=True,
            selected=True,
        ).select_related('account'))
        if len(pages) != len(page_ids):
            raise serializers.ValidationError({'target_pages': 'Each target must be an available, selected Page on your connected Facebook account.'})
        for page in pages:
            if page.account.token_expires_at and page.account.token_expires_at <= timezone.now():
                raise serializers.ValidationError({'target_pages': 'Your Facebook connection has expired. Reconnect before scheduling.'})
            try:
                usable_access_token(page.account)
            except FacebookIntegrationError:
                raise serializers.ValidationError({'target_pages': 'Your Facebook connection needs attention. Reconnect before scheduling.'}) from None
        attrs['_validated_pages'] = pages
        return attrs

    def create(self, validated_data):
        from django.db import transaction

        pages = validated_data.pop('_validated_pages')
        validated_data.pop('target_pages', None)
        key = validated_data.pop('idempotency_key', None) or uuid.uuid4()
        owner = self.context['request'].user
        with transaction.atomic():
            defaults = {
                'owner': owner,
                'media_title_snapshot': validated_data['media'].title,
                **validated_data,
            }
            prior, created = ScheduledPost.objects.get_or_create(
                idempotency_key=key,
                defaults=defaults,
            )
            if not created:
                same_targets = set(prior.targets.values_list('page_id', flat=True)) == {page.pk for page in pages}
                same_content = (
                    prior.owner_id == owner.pk
                    and prior.media_id == validated_data['media'].pk
                    and prior.scheduled_for == validated_data['scheduled_for']
                    and prior.caption == validated_data.get('caption', '')
                    and prior.description == validated_data.get('description', '')
                    and same_targets
                )
                if not same_content:
                    raise serializers.ValidationError({'idempotency_key': 'This request key was already used for a different post.'})
                return prior
            ScheduledPostTarget.objects.bulk_create([
                ScheduledPostTarget(
                    post=prior,
                    page=page,
                    page_id_snapshot=page.page_id,
                    page_name_snapshot=page.name,
                )
                for page in pages
            ])
        return prior

    def update(self, instance, validated_data):
        from django.db import transaction

        pages = validated_data.pop('_validated_pages')
        validated_data.pop('target_pages', None)
        validated_data.pop('idempotency_key', None)
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.media_title_snapshot = instance.media.title
            instance.save()
            instance.targets.all().delete()
            ScheduledPostTarget.objects.bulk_create([
                ScheduledPostTarget(
                    post=instance,
                    page=page,
                    page_id_snapshot=page.page_id,
                    page_name_snapshot=page.name,
                )
                for page in pages
            ])
        return instance


class PublishingAttemptSerializer(serializers.ModelSerializer):
    post_id = serializers.IntegerField(source='target.post_id', read_only=True)
    page_id = serializers.CharField(source='target.page_id_snapshot', read_only=True)
    page_name = serializers.CharField(source='target.page_name_snapshot', read_only=True)
    media_title = serializers.CharField(source='target.post.media_title_snapshot', read_only=True)

    class Meta:
        model = PublishingAttempt
        fields = ['id', 'post_id', 'media_title', 'page_id', 'page_name', 'attempt_number', 'status', 'error', 'external_post_id', 'started_at', 'finished_at']
        read_only_fields = fields
