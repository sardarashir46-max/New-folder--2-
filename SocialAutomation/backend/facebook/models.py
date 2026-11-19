from django.conf import settings
from django.db import models

from .tokens import decrypt_token, encrypt_token


class FacebookAccount(models.Model):
    class Status(models.TextChoices):
        CONNECTED = 'connected', 'Connected'
        EXPIRED = 'expired', 'Expired'
        REVOKED = 'revoked', 'Revoked'
        ERROR = 'error', 'Needs attention'

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='facebook_account',
    )
    facebook_user_id = models.CharField(max_length=100)
    access_token_encrypted = models.TextField()
    token_expires_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CONNECTED)
    granted_permissions = models.JSONField(default=list, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def set_access_token(self, token):
        self.access_token_encrypted = encrypt_token(token)

    def get_access_token(self):
        return decrypt_token(self.access_token_encrypted)

    def __str__(self):
        return f'Facebook connection for {self.owner}'


class FacebookPage(models.Model):
    account = models.ForeignKey(FacebookAccount, on_delete=models.CASCADE, related_name='pages')
    page_id = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=255, blank=True)
    picture_url = models.URLField(max_length=1000, blank=True)
    tasks = models.JSONField(default=list, blank=True)
    selected = models.BooleanField(default=False)
    available = models.BooleanField(default=True)
    page_access_token_encrypted = models.TextField(blank=True)
    page_token_expires_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'page_id']
        constraints = [
            models.UniqueConstraint(fields=['account', 'page_id'], name='unique_facebook_page_per_account'),
        ]

    def set_page_access_token(self, token):
        self.page_access_token_encrypted = encrypt_token(token) if token else ''

    def get_page_access_token(self):
        if not self.page_access_token_encrypted:
            return ''
        return decrypt_token(self.page_access_token_encrypted)

    def __str__(self):
        return self.name
