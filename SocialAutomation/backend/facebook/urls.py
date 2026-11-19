from django.urls import path

from . import views

urlpatterns = [
    path('oauth/start/', views.oauth_start, name='facebook-oauth-start'),
    path('oauth/callback/', views.oauth_callback, name='facebook-oauth-callback'),
    path('connection/', views.FacebookConnectionView.as_view(), name='facebook-connection'),
    path('pages/', views.FacebookPagesView.as_view(), name='facebook-pages'),
    path('pages/refresh/', views.FacebookPagesRefreshView.as_view(), name='facebook-pages-refresh'),
    path('pages/<int:page_id>/selection/', views.FacebookPageSelectionView.as_view(), name='facebook-page-selection'),
]
