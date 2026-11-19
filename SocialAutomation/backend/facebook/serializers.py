from rest_framework import serializers

from .models import FacebookAccount, FacebookPage


class FacebookPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = FacebookPage
        fields = [
            'id',
            'page_id',
            'name',
            'category',
            'picture_url',
            'tasks',
            'selected',
            'available',
            'page_token_expires_at',
            'last_refreshed_at',
        ]
        read_only_fields = fields


class FacebookConnectionSerializer(serializers.ModelSerializer):
    page_count = serializers.SerializerMethodField()
    selected_page_count = serializers.SerializerMethodField()

    class Meta:
        model = FacebookAccount
        fields = [
            'facebook_user_id',
            'status',
            'token_expires_at',
            'granted_permissions',
            'connected_at',
            'page_count',
            'selected_page_count',
        ]
        read_only_fields = fields

    def get_selected_page_count(self, account):
        return account.pages.filter(available=True, selected=True).count()

    def get_page_count(self, account):
        return account.pages.filter(available=True).count()
