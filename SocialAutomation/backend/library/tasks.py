"""Due-post dispatch and idempotent publishing worker tasks."""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import PublishingAttempt, ScheduledPost, ScheduledPostTarget
from .services import (
    FacebookPublishingService,
    PermanentPublishingError,
    TemporaryPublishingError,
    UncertainPublishingError,
    validate_target_is_still_authorized,
)

logger = logging.getLogger(__name__)


def _queue_post(post_id, queued_at):
    """Send a queued post to Celery, releasing its claim if Redis is unavailable."""
    try:
        process_scheduled_post.apply_async(
            args=(post_id,),
            task_id=f'scheduled-post-{post_id}',
        )
    except Exception as error:
        logger.error('Could not queue scheduled post %s (%s).', post_id, type(error).__name__)
        ScheduledPost.objects.filter(
            pk=post_id,
            status=ScheduledPost.Status.QUEUED,
            queue_claimed_at=queued_at,
        ).update(
            status=ScheduledPost.Status.PENDING,
            queue_claimed_at=None,
            last_error='The background queue is unavailable. The post will be retried later.',
            updated_at=timezone.now(),
        )


@shared_task(ignore_result=True)
def dispatch_due_scheduled_posts():
    """Claim due posts once and enqueue them without doing publishing in a request."""
    if not settings.PUBLISHING_ENABLED or not settings.PUBLISHING_DISPATCH_ENABLED:
        return 0
    now = timezone.now()
    stale_before = now - timedelta(seconds=settings.PUBLISHING_DISPATCH_STALE_SECONDS)
    candidates = list(ScheduledPost.objects.filter(
        Q(status=ScheduledPost.Status.PENDING, scheduled_for__lte=now)
        | Q(status=ScheduledPost.Status.QUEUED, queue_claimed_at__lte=stale_before)
    ).values_list('pk', flat=True))
    queued = 0
    for post_id in candidates:
        claim_time = timezone.now()
        with transaction.atomic():
            # Conditional updates are an atomic claim on SQLite too, where select_for_update is ignored.
            claimed = ScheduledPost.objects.filter(
                pk=post_id,
                status=ScheduledPost.Status.PENDING,
                scheduled_for__lte=now,
            ).update(
                status=ScheduledPost.Status.QUEUED,
                queue_claimed_at=claim_time,
                updated_at=claim_time,
            )
            if not claimed:
                claimed = ScheduledPost.objects.filter(
                    pk=post_id,
                    status=ScheduledPost.Status.QUEUED,
                    queue_claimed_at__lte=stale_before,
                ).update(queue_claimed_at=claim_time, updated_at=claim_time)
            if not claimed:
                continue
            transaction.on_commit(lambda post_id=post_id, claim_time=claim_time: _queue_post(post_id, claim_time))
            queued += 1
    return queued


def _finish_attempt(attempt, target, *, status, error='', retryable=False):
    now = timezone.now()
    with transaction.atomic():
        attempt.status = status
        attempt.error = error
        attempt.finished_at = now
        attempt.save(update_fields=['status', 'error', 'finished_at'])
        target.status = ScheduledPostTarget.Status.PENDING if retryable else ScheduledPostTarget.Status.FAILED
        target.last_error = error
        if retryable:
            target.retry_count += 1
        target.save(update_fields=['status', 'last_error', 'retry_count', 'updated_at'])


def _refresh_post_status(post_id):
    post = ScheduledPost.objects.filter(pk=post_id).first()
    if post is None or post.status == ScheduledPost.Status.CANCELLED:
        return
    statuses = list(post.targets.values_list('status', flat=True))
    if not statuses:
        post.status = ScheduledPost.Status.FAILED
        post.last_error = 'This scheduled post has no available Page targets.'
    elif any(value == ScheduledPostTarget.Status.REVIEW_REQUIRED for value in statuses):
        post.status = ScheduledPost.Status.REVIEW_REQUIRED
        errors = list(post.targets.exclude(last_error='').values_list('last_error', flat=True)[:1])
        post.last_error = errors[0] if errors else 'Review Page activity before taking further action.'
    elif any(value in (ScheduledPostTarget.Status.PENDING, ScheduledPostTarget.Status.PROCESSING) for value in statuses):
        post.status = ScheduledPost.Status.PROCESSING
    elif all(value == ScheduledPostTarget.Status.PUBLISHED for value in statuses):
        post.status = ScheduledPost.Status.PUBLISHED
        post.published_at = timezone.now()
        post.last_error = ''
    elif any(value == ScheduledPostTarget.Status.PUBLISHED for value in statuses):
        post.status = ScheduledPost.Status.PARTIAL
        post.published_at = post.targets.filter(
            status=ScheduledPostTarget.Status.PUBLISHED,
        ).order_by('-published_at').values_list('published_at', flat=True).first()
        errors = list(post.targets.exclude(last_error='').values_list('last_error', flat=True)[:1])
        post.last_error = errors[0] if errors else ''
    else:
        post.status = ScheduledPost.Status.FAILED
        errors = list(post.targets.exclude(last_error='').values_list('last_error', flat=True)[:1])
        post.last_error = errors[0] if errors else 'Publishing failed for all selected Pages.'
    post.save(update_fields=['status', 'published_at', 'last_error', 'updated_at'])


@shared_task(bind=True, max_retries=4, ignore_result=True)
def process_scheduled_post(self, post_id):
    """Run page targets independently; duplicate deliveries cannot claim a target twice."""
    if not settings.PUBLISHING_ENABLED:
        return
    try:
        with transaction.atomic():
            post = ScheduledPost.objects.select_for_update().filter(pk=post_id).first()
            if post is None or post.status in (
                ScheduledPost.Status.CANCELLED,
                ScheduledPost.Status.PUBLISHED,
                ScheduledPost.Status.PARTIAL,
                ScheduledPost.Status.FAILED,
            ):
                return
            if post.status == ScheduledPost.Status.QUEUED:
                claimed = ScheduledPost.objects.filter(
                    pk=post_id,
                    status=ScheduledPost.Status.QUEUED,
                ).update(
                    status=ScheduledPost.Status.PROCESSING,
                    processing_task_id=self.request.id or '',
                    updated_at=timezone.now(),
                )
                if not claimed:
                    return
            elif post.status != ScheduledPost.Status.PROCESSING:
                return

        target_ids = list(ScheduledPostTarget.objects.filter(
            post_id=post_id,
            status=ScheduledPostTarget.Status.PENDING,
        ).values_list('pk', flat=True))
        if not target_ids:
            _refresh_post_status(post_id)
            return

        temporary_failure = False
        publisher = FacebookPublishingService()
        for target_id in target_ids:
            with transaction.atomic():
                claimed = ScheduledPostTarget.objects.filter(
                    pk=target_id,
                    status=ScheduledPostTarget.Status.PENDING,
                ).update(status=ScheduledPostTarget.Status.PROCESSING, updated_at=timezone.now())
                if not claimed:
                    continue
                target = ScheduledPostTarget.objects.select_related('post', 'post__media', 'page', 'page__account').get(pk=target_id)
                attempt_number = target.attempts.count() + 1
                attempt = PublishingAttempt.objects.create(
                    target=target,
                    attempt_number=attempt_number,
                    idempotency_key=f'{target.idempotency_key}:{attempt_number}',
                )

            authorization_error = validate_target_is_still_authorized(target)
            try:
                if authorization_error:
                    raise PermanentPublishingError(authorization_error)
                result = publisher.publish(target, str(target.idempotency_key))
                # The current adapter always raises. A future adapter must return an explicit Meta ID.
                external_id = result.get('external_post_id') if isinstance(result, dict) and result.get('confirmed') is True else None
                if not external_id:
                    raise PermanentPublishingError('Meta did not confirm publication. No success was recorded.')
                with transaction.atomic():
                    target.status = ScheduledPostTarget.Status.PUBLISHED
                    target.external_post_id = str(external_id)
                    target.published_at = timezone.now()
                    target.last_error = ''
                    target.save(update_fields=['status', 'external_post_id', 'published_at', 'last_error', 'updated_at'])
                    attempt.status = PublishingAttempt.Status.PUBLISHED
                    attempt.external_post_id = str(external_id)
                    attempt.finished_at = timezone.now()
                    attempt.save(update_fields=['status', 'external_post_id', 'finished_at'])
            except TemporaryPublishingError:
                # Keep provider details and credentials out of user-visible attempt records.
                safe_error = 'Temporary publishing service error. A retry will be attempted.'
                if self.request.retries >= settings.PUBLISHING_MAX_RETRIES:
                    _finish_attempt(attempt, target, status=PublishingAttempt.Status.FAILED,
                                    error='A temporary publishing error persisted after the retry limit.')
                else:
                    _finish_attempt(attempt, target, status=PublishingAttempt.Status.RETRYING,
                                    error=safe_error, retryable=True)
                    temporary_failure = True
                    break
            except PermanentPublishingError as error:
                _finish_attempt(attempt, target, status=PublishingAttempt.Status.FAILED, error=str(error)[:1000])
            except UncertainPublishingError as error:
                now = timezone.now()
                target.status = ScheduledPostTarget.Status.REVIEW_REQUIRED
                target.last_error = str(error)[:1000]
                target.save(update_fields=['status', 'last_error', 'updated_at'])
                attempt.status = PublishingAttempt.Status.UNCERTAIN
                attempt.error = target.last_error
                attempt.finished_at = now
                attempt.save(update_fields=['status', 'error', 'finished_at'])
            except Exception:
                # Never log an arbitrary third-party exception: it could contain credentials.
                _finish_attempt(
                    attempt,
                    target,
                    status=PublishingAttempt.Status.FAILED,
                    error='An unexpected publishing error occurred. No success was recorded.',
                )
                logger.error('Unexpected publishing error for scheduled post %s target %s.', post_id, target_id)

        if temporary_failure is not None and self.request.retries < settings.PUBLISHING_MAX_RETRIES:
            ScheduledPost.objects.filter(pk=post_id, status=ScheduledPost.Status.PROCESSING).update(
                status=ScheduledPost.Status.QUEUED,
                queue_claimed_at=timezone.now(),
                retry_count=self.request.retries + 1,
                last_error='A temporary publishing error occurred. Waiting to retry.',
                updated_at=timezone.now(),
            )
            raise self.retry(
                max_retries=settings.PUBLISHING_MAX_RETRIES,
                countdown=min(60 * (2 ** self.request.retries), 3600),
            )
        _refresh_post_status(post_id)
    except self.MaxRetriesExceededError:
        ScheduledPost.objects.filter(pk=post_id).update(
            status=ScheduledPost.Status.FAILED,
            last_error='Publishing stopped after the configured retry limit.',
            updated_at=timezone.now(),
        )


@shared_task(ignore_result=True)
def recover_stale_processing_targets():
    """A crashed worker may have sent the request; require review instead of reposting."""
    cutoff = timezone.now() - timedelta(seconds=settings.PUBLISHING_DISPATCH_STALE_SECONDS)
    stale = ScheduledPostTarget.objects.filter(
        status=ScheduledPostTarget.Status.PROCESSING,
        updated_at__lte=cutoff,
    ).select_related('post')
    post_ids = set()
    for target in stale:
        now = timezone.now()
        changed = ScheduledPostTarget.objects.filter(pk=target.pk, status=ScheduledPostTarget.Status.PROCESSING).update(
            status=ScheduledPostTarget.Status.REVIEW_REQUIRED,
            last_error='Worker stopped during publishing. Check the Facebook Page before retrying.',
            updated_at=now,
        )
        if changed:
            attempt = target.attempts.filter(status=PublishingAttempt.Status.PROCESSING).first()
            if attempt:
                attempt.status = PublishingAttempt.Status.UNCERTAIN
                attempt.error = 'Worker stopped during publishing. Publication outcome requires review.'
                attempt.finished_at = now
                attempt.save(update_fields=['status', 'error', 'finished_at'])
            post_ids.add(target.post_id)
    for post_id in post_ids:
        _refresh_post_status(post_id)
    return len(post_ids)
