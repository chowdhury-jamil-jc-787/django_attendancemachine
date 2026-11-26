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


class AttendanceNotificationEvent(models.Model):
    """Track one scheduled notification per user, day, and event type.

    The scheduled notification service claims an event before contacting
    Firebase, then marks it sent or failed. This prevents the every-minute
    cron job from repeatedly sending successful reminders.
    """

    EVENT_TYPE_CHOICES = [
        ("missing_0700", "Missing attendance - 7:00 AM"),
        ("missing_0745", "Missing attendance - 7:45 AM"),
        ("missing_0900", "Missing attendance - 9:00 AM"),
        ("noon_missing_admin", "Missing attendance summary - noon"),
        ("workday_08h", "Workday reminder - 8 hours"),
        ("workday_09h", "Workday reminder - 9 hours"),
        ("workday_10h", "Workday reminder - 10 hours"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_notification_events",
    )
    attendance_date = models.DateField(db_index=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES)

    # Deterministic key: YYYY-MM-DD:user_id:event_type.
    # 191 characters is compatible with MySQL unique indexes.
    event_key = models.CharField(max_length=191, unique=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_notification_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "attendance_date"],
                name="attnev_user_date_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.attendance_date} - {self.event_type} ({self.status})"
