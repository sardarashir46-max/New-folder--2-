from django.contrib import admin

from .models import Channel


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("title", "channel_id", "owner", "connection_status")
    list_filter = ("connection_status",)
    search_fields = ("title", "channel_id", "owner__username")
