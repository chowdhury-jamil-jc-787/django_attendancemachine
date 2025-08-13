# leave/models.py
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from datetime import date as date_cls

class Leave(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('full_day', 'Full Day'),
        ('1st_half', 'First Half'),
        ('2nd_half', 'Second Half'),
    ]

    FULL_DAY_REASONS = [
        ('personal', 'Personal Leave'),
        ('family', 'Family Issue'),
        ('sick', 'Sick Leave'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leaves')
    leave_type = models.CharField(max_length=10, choices=LEAVE_TYPE_CHOICES)
    reason = models.CharField(max_length=50)

    # ✅ Array of ISO date strings, e.g., ["2025-08-20"] or ["2025-08-20","2025-08-22"]
    date = models.JSONField(default=list, blank=True)

    # Status
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    is_approved = models.BooleanField(default=False)

    # Optional fields kept from your previous version
    informed_status = models.CharField(max_length=50, null=True, blank=True)
    email_body = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # --- helpers ---
    @staticmethod
    def _to_iso(d):
        if isinstance(d, str):
            return d
        if isinstance(d, date_cls):
            return d.isoformat()
        raise ValidationError("Each date must be a valid ISO date string (YYYY-MM-DD).")

    @staticmethod
    def _validate_iso(s):
        try:
            date_cls.fromisoformat(s)
        except Exception:
            raise ValidationError(f"Invalid date value: {s}. Expect YYYY-MM-DD.")

    def clean(self):
        # Normalize to list
        if self.date is None or self.date == "":
            self.date = []
        if isinstance(self.date, str):
            self.date = [self.date]
        if not isinstance(self.date, list) or len(self.date) == 0:
            raise ValidationError("Provide at least one date.")

        # Validate each date
        normalized = []
        for d in self.date:
            if isinstance(d, date_cls):
                d = d.isoformat()
            try:
                date_cls.fromisoformat(d)
            except Exception:
                raise ValidationError(f"Invalid date value: {d}")
            normalized.append(d)

        # Dedup + sort
        normalized = sorted(set(normalized))
        self.date = normalized

        # 🔒 Half-day must be exactly one date
        if self.leave_type in ('1st_half', '2nd_half') and len(self.date) != 1:
            raise ValidationError("Half-day leave requires exactly one date.")

        # Full-day reason business rule
        if self.leave_type == 'full_day' and self.reason not in dict(self.FULL_DAY_REASONS):
            raise ValidationError("Invalid reason for full-day leave. Use personal/family/sick.")

        # Overlap prevention with pending/approved
        existing = Leave.objects.filter(
            user=self.user,
            status__in=['approved', 'pending']
        ).exclude(id=self.id)

        for other in existing:
            other_dates = set(other.date or [])
            if any(d in other_dates for d in self.date):
                # (Optional same-day 1st_half + 2nd_half allowance — mirror serializer if you enable it)
                # if (self.leave_type in ('1st_half', '2nd_half') and len(self.date) == 1 and
                #     other.date and len(other.date) == 1 and
                #     self.date[0] == other.date[0] and
                #     {self.leave_type, other.leave_type} == {'1st_half', '2nd_half'}):
                #     continue
                raise ValidationError("Leave already exists on one or more selected dates.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        sample = ", ".join(self.date[:3])
        suffix = "..." if len(self.date) > 3 else ""
        return f"{self.user.username} - {self.get_leave_type_display()} - [{sample}{suffix}] - {self.get_status_display()}"
