from rest_framework.routers import DefaultRouter
from django.urls import include, path

from .views import (
    DashboardSummaryView,
    MediaAssetViewSet,
    PublishingHistoryView,
    ScheduledPostViewSet,
)

media_router = DefaultRouter()
media_router.register('media', MediaAssetViewSet, basename='mediaasset')
post_router = DefaultRouter()
post_router.register('posts', ScheduledPostViewSet, basename='scheduledpost')

urlpatterns = [
    path('dashboard/summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('publishing/history/', PublishingHistoryView.as_view(), name='publishing-history'),
    path('library/', include(media_router.urls)),
    path('publishing/', include(post_router.urls)),
]
