"""Authenticated, owner-scoped API for private media and scheduled posts."""
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from channels.models import Channel
from facebook.models import FacebookAccount, FacebookPage

from .models import MediaAsset, PublishingAttempt, ScheduledPost, ScheduledPostTarget
from .serializers import (
    MediaAssetSerializer,
    MediaRightsSerializer,
    PublishingAttemptSerializer,
    ScheduledPostSerializer,
)
from .services import approve_media_declaration


class MediaAssetViewSet(viewsets.ModelViewSet):
    serializer_class = MediaAssetSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = MediaAsset.objects.filter(owner=self.request.user)
        search = self.request.query_params.get('search', '').strip()
        rights = self.request.query_params.get('rights_status')
        processing = self.request.query_params.get('processing_status')
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search) | Q(caption__icontains=search))
        if rights:
            queryset = queryset.filter(rights_status=rights)
        if processing:
            queryset = queryset.filter(processing_status=processing)
        return queryset

    def perform_create(self, serializer):
        upload = serializer.validated_data['file']
        serializer.save(owner=self.request.user, file_size=upload.size, processing_status=MediaAsset.ProcessingStatus.READY)

    def perform_update(self, serializer):
        media = self.get_object()
        if ScheduledPost.objects.filter(media=media, status__in=[
            ScheduledPost.Status.QUEUED,
            ScheduledPost.Status.PROCESSING,
        ]).exists():
            raise ValidationError('This video cannot be edited while a scheduled post is processing.')
        serializer.save()

    def _cancel_unstarted_schedules(self, media, reason):
        pending_posts = ScheduledPost.objects.select_for_update().filter(
            media=media,
            status__in=[ScheduledPost.Status.PENDING, ScheduledPost.Status.QUEUED],
        )
        pending_ids = list(pending_posts.values_list('pk', flat=True))
        # Lock queued rows first, then recheck. A worker either started before this
        # check (and blocks the change) or waits until these posts are cancelled.
        if ScheduledPost.objects.filter(media=media, status=ScheduledPost.Status.PROCESSING).exists():
            raise ValidationError('This video cannot change while a scheduled post is processing.')
        pending_posts.update(
            status=ScheduledPost.Status.CANCELLED,
            last_error=reason,
            updated_at=timezone.now(),
        )
        ScheduledPostTarget.objects.filter(
            post_id__in=pending_ids,
            status=ScheduledPostTarget.Status.PENDING,
        ).update(status=ScheduledPostTarget.Status.CANCELLED, updated_at=timezone.now())

    @action(detail=True, methods=['get'], url_path='file')
    def file_content(self, request, pk=None):
        media = self.get_object()
        try:
            video = media.file.open('rb')
        except (OSError, ValueError):
            return Response({'detail': 'The stored video file is unavailable.'}, status=status.HTTP_404_NOT_FOUND)
        response = FileResponse(video, content_type=media.detected_content_type)
        extension = media.file.name.rsplit('.', 1)[-1].lower()
        response['Content-Disposition'] = f'inline; filename="video-{media.pk}.{extension}"'
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        return response

    @action(detail=True, methods=['patch'], url_path='rights')
    def rights(self, request, pk=None):
        media = self.get_object()
        serializer = MediaRightsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            media = MediaAsset.objects.select_for_update().get(pk=media.pk, owner=request.user)
            if serializer.validated_data['rights_status'] not in (MediaAsset.RightsStatus.OWNED, MediaAsset.RightsStatus.AUTHORIZED):
                self._cancel_unstarted_schedules(
                    media,
                    'The media rights declaration changed. Review rights and schedule again.',
                )
            media = approve_media_declaration(media, request.user, serializer.validated_data['rights_status'])
        return Response(self.get_serializer(media).data)

    def destroy(self, request, *args, **kwargs):
        media = self.get_object()
        with transaction.atomic():
            media = MediaAsset.objects.select_for_update().get(pk=media.pk, owner=request.user)
            self._cancel_unstarted_schedules(media, 'The scheduled video was removed from the library.')
            stored_file = media.file
            media.delete()
            transaction.on_commit(lambda: stored_file.delete(save=False), robust=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ScheduledPostViewSet(viewsets.ModelViewSet):
    serializer_class = ScheduledPostSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        queryset = ScheduledPost.objects.filter(owner=self.request.user).prefetch_related('targets')
        post_status = self.request.query_params.get('status')
        if post_status:
            queryset = queryset.filter(status=post_status)
        return queryset

    def perform_update(self, serializer):
        with transaction.atomic():
            post = ScheduledPost.objects.select_for_update().get(pk=self.get_object().pk, owner=self.request.user)
            if post.status != ScheduledPost.Status.PENDING:
                raise ValidationError('Only pending scheduled posts can be edited.')
            if PublishingAttempt.objects.filter(target__post=post).exists():
                raise ValidationError('Posts with publishing attempts cannot be edited.')
            serializer.instance = post
            serializer.save()

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        post = self.get_object()
        with transaction.atomic():
            changed = ScheduledPost.objects.filter(
                pk=post.pk,
                owner=request.user,
                status__in=[ScheduledPost.Status.PENDING, ScheduledPost.Status.QUEUED],
            ).update(
                status=ScheduledPost.Status.CANCELLED,
                last_error='',
                updated_at=timezone.now(),
            )
            if not changed:
                raise ValidationError('Only pending or queued scheduled posts can be cancelled.')
            post.targets.filter(status=ScheduledPostTarget.Status.PENDING).update(
                status=ScheduledPostTarget.Status.CANCELLED,
                updated_at=timezone.now(),
            )
        post.refresh_from_db()
        return Response(self.get_serializer(post).data)

    def destroy(self, request, *args, **kwargs):
        # Deletion aliases cancellation so queued work and its audit trail are preserved.
        return self.cancel(request, pk=kwargs['pk'])


class PublishingHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        attempts = PublishingAttempt.objects.filter(
            target__post__owner=request.user,
        ).select_related('target', 'target__post').order_by('-started_at')[:200]
        return Response(PublishingAttemptSerializer(attempts, many=True).data)


class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        now = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        posts = ScheduledPost.objects.filter(owner=request.user)
        upcoming = posts.filter(
            status__in=[ScheduledPost.Status.PENDING, ScheduledPost.Status.QUEUED, ScheduledPost.Status.PROCESSING],
            scheduled_for__gte=now,
        ).prefetch_related('targets')[:4]
        return Response({
            'youtube_channels': Channel.objects.filter(owner=request.user).count(),
            'youtube_connected': Channel.objects.filter(owner=request.user, connection_status=Channel.ConnectionStatus.CONNECTED).exists(),
            'facebook_connected': FacebookAccount.objects.filter(owner=request.user, status=FacebookAccount.Status.CONNECTED).exists(),
            'facebook_pages': FacebookPage.objects.filter(
                account__owner=request.user,
                account__status='connected',
                available=True,
                selected=True,
            ).count(),
            'media_items': MediaAsset.objects.filter(owner=request.user).count(),
            'scheduled_posts': posts.filter(status__in=[
                ScheduledPost.Status.PENDING,
                ScheduledPost.Status.QUEUED,
                ScheduledPost.Status.PROCESSING,
            ]).count(),
            'published_this_month': posts.filter(
                status=ScheduledPost.Status.PUBLISHED,
                published_at__gte=month_start,
            ).count(),
            'upcoming_posts': [{
                'id': post.pk,
                'title': post.media_title_snapshot,
                'scheduled_for': post.scheduled_for,
                'status': post.status,
                'pages': [target.page_name_snapshot for target in post.targets.all()],
            } for post in upcoming],
        })
