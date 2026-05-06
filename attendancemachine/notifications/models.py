# notifications/models.py

import hashlib
from django.conf import settings
from django.db import models


class UserDeviceToken(models.Model):
    PLATFORM_CHOICES = [
        ("android", "Android"),
        ("ios", "iOS"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens"
    )

    # Store full FCM token here
    token = models.TextField()

    # Unique fixed-length hash for MySQL indexing
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="android")
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_device_token"

    def save(self, *args, **kwargs):
        if self.token:
            self.token_hash = hashlib.sha256(self.token.encode("utf-8")).hexdigest()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user_id} - {self.platform}"


class AttendancePunchNotificationLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="punch_notification_logs"
    )

    emp_code = models.CharField(max_length=50)
    punch_time = models.DateTimeField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_punch_notification_log"
        unique_together = ("emp_code", "punch_time")

    def __str__(self):
        return f"{self.emp_code} - {self.punch_time}"