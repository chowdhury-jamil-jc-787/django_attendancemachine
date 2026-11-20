import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class BlacklistedAccessToken(models.Model):
    jti = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class DeviceSession(models.Model):
    """Store and manage individual device login sessions."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_sessions"
    )

    device_name = models.CharField(
        max_length=160,
        default="Unknown device"
    )

    user_agent = models.CharField(
        max_length=512,
        blank=True
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        default=timezone.now
    )

    last_seen_at = models.DateTimeField(
        default=timezone.now
    )

    expires_at = models.DateTimeField()

    revoked_at = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["-last_seen_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["user", "revoked_at"],
                name="device_user_active_idx"
            ),
        ]

    @property
    def is_active(self):
        return (
            self.revoked_at is None
            and self.expires_at > timezone.now()
        )

    def __str__(self):
        return f"{self.user_id} - {self.device_name} ({self.id})"
