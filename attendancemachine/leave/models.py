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
        ('paternity', 'Paternity Leave'),
        ('maternity', 'Maternity Leave'),
        ('wedding', 'Wedding Leave')
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancel_pending', 'Cancel Pending'),
        ('cancelled', 'Cancelled'),       # ✅ allow fully cancelled leave with date=[]
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leaves')
    leave_type = models.CharField(max_length=10, choices=LEAVE_TYPE_CHOICES)
    reason = models.CharField(max_length=50)

    # JSON array of ISO date strings
    date = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    is_approved = models.BooleanField(default=False)

    informed_status = models.CharField(max_length=50, null=True, blank=True)
    email_body = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ------------------------
    # VALIDATION LOGIC
    # ------------------------
    def clean(self):
        # Normalize date to list
        if self.date is None or self.date == "":
            self.date = []

        if isinstance(self.date, str):
            self.date = [self.date]

        # ✅ If cancelled → allow empty date list, stop validation
        if self.status == "cancelled":
            if not isinstance(self.date, list):
                raise ValidationError("Internal error: date must be a list.")
            return  # skip all normal validations

        # ---------------------------
        # For all OTHER states:
        # ---------------------------
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

        # Deduplicate + sort
        normalized = sorted(set(normalized))
        self.date = normalized

        # Half-day rule
        if self.leave_type in ('1st_half', '2nd_half') and len(self.date) != 1:
            raise ValidationError("Half-day leave requires exactly one date.")

        # Full-day reason rule
        if self.leave_type == 'full_day' and self.reason not in dict(self.FULL_DAY_REASONS):
            raise ValidationError("Invalid reason for full-day leave. Use personal/family/sick/paternity/maternity/wedding.")

        # Prevent overlapping leave
        existing = Leave.objects.filter(
            user=self.user,
            status__in=['approved', 'pending']
        ).exclude(id=self.id)

        for other in existing:
            other_dates = set(other.date or [])
            if any(d in other_dates for d in self.date):
                raise ValidationError("Leave already exists on one or more selected dates.")

    # ------------------------
    # SAVE OVERRIDE
    # ------------------------
    def save(self, *args, **kwargs):
        self.full_clean()  # applies validation
        super().save(*args, **kwargs)

    def __str__(self):
        sample = ", ".join(self.date[:3])
        suffix = "..." if len(self.date) > 3 else ""
        return f"{self.user.username} - {self.get_leave_type_display()} - [{sample}{suffix}] - {self.get_status_display()}"
