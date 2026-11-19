from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import Channel
from .serializers import ChannelSerializer


class ChannelViewSet(viewsets.ModelViewSet):
    serializer_class = ChannelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Filtering here protects list, detail, update, and delete operations.
        return Channel.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        # The client cannot choose another user's owner value.
        serializer.save(owner=self.request.user)
