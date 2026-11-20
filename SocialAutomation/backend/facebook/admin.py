from django.contrib import admin

from .models import FacebookAccount, FacebookPage


@admin.register(FacebookAccount)
class FacebookAccountAdmin(admin.ModelAdmin):
    list_display = ('owner', 'facebook_user_id', 'status', 'token_expires_at', 'connected_at')
    list_filter = ('status',)
    readonly_fields = ('access_token_encrypted',)
    exclude = ('access_token_encrypted',)
    search_fields = ('owner__username', 'facebook_user_id')


@admin.register(FacebookPage)
class FacebookPageAdmin(admin.ModelAdmin):
    list_display = ('name', 'page_id', 'account', 'selected', 'available')
    list_filter = ('selected', 'available')
    readonly_fields = ('page_access_token_encrypted',)
    exclude = ('page_access_token_encrypted',)
    search_fields = ('name', 'page_id', 'account__owner__username')
