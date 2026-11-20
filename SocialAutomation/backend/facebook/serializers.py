from rest_framework import serializers

from .models import FacebookAccount, FacebookPage


class FacebookPageSerializer(serializers.ModelSerializer):
    can_create_content = serializers.SerializerMethodField()

    def get_can_create_content(self, page):
        return 'CREATE_CONTENT' in page.tasks

    class Meta:
        model = FacebookPage
        fields = [
            'id',
            'page_id',
            'name',
            'category',
            'picture_url',
            'tasks',
            'can_create_content',
            'selected',
            'available',
            'page_token_expires_at',
            'last_refreshed_at',
        ]
        read_only_fields = fields


class FacebookConnectionSerializer(serializers.ModelSerializer):
    page_count = serializers.SerializerMethodField()
    selected_page_count = serializers.SerializerMethodField()
    publishing_permissions_granted = serializers.SerializerMethodField()
    requires_reconnection = serializers.SerializerMethodField()

    def get_publishing_permissions_granted(self, account):
        required = {'pages_show_list', 'pages_read_engagement', 'pages_manage_posts'}
        return required.issubset(set(account.granted_permissions or []))

    def get_requires_reconnection(self, account):
        return not self.get_publishing_permissions_granted(account)

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
            'publishing_permissions_granted',
            'requires_reconnection',
        ]
        read_only_fields = fields

    def get_selected_page_count(self, account):
        return account.pages.filter(available=True, selected=True).count()

    def get_page_count(self, account):
        return account.pages.filter(available=True).count()
