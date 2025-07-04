from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
import datetime

class Leave(models.Model):
    LEAVE_TYPE_CHOICES = [
        ('full_day', 'Full Day'),
        ('half_day', 'Half Day'),
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

    # Dates
    date = models.DateField(null=True, blank=True)  # For half-day
    start_date = models.DateField(null=True, blank=True)  # For full-day
    end_date = models.DateField(null=True, blank=True)

    # Status
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    is_approved = models.BooleanField(default=False)

    # Optional email body for debugging/audit
    email_body = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        # Handle date logic
        if self.leave_type == 'half_day':
            if not self.date:
                raise ValidationError("Half-day leave must have a specific date.")
            if self.start_date or self.end_date:
                raise ValidationError("Half-day leave cannot have a date range.")
            dates_to_check = [self.date]

        elif self.leave_type == 'full_day':
            if self.start_date and self.end_date:
                if self.start_date > self.end_date:
                    raise ValidationError("Start date cannot be after end date.")
            elif self.date:
                self.start_date = self.end_date = self.date  # Convert single date
            else:
                raise ValidationError("Full-day leave requires a date or range.")

            if self.reason not in dict(self.FULL_DAY_REASONS):
                raise ValidationError("Invalid reason for full-day leave.")

            # Build range for validation
            if self.start_date and self.end_date:
                delta = (self.end_date - self.start_date).days + 1
                dates_to_check = [self.start_date + datetime.timedelta(days=i) for i in range(delta)]
            else:
                dates_to_check = []

        else:
            raise ValidationError("Invalid leave type.")

        # Check for overlap with approved leaves
        overlapping = Leave.objects.filter(user=self.user, is_approved=True).exclude(id=self.id)

        if self.leave_type == 'half_day':
            overlapping = overlapping.filter(date__in=dates_to_check)
        elif self.start_date and self.end_date:
            overlapping = overlapping.filter(
                models.Q(start_date__lte=self.end_date) & models.Q(end_date__gte=self.start_date)
            )

        if overlapping.exists():
            raise ValidationError("Leave already exists on selected dates.")

    def save(self, *args, **kwargs):
        self.full_clean()  # Ensure validation is triggered
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.get_leave_type_display()} - {self.get_status_display()}"
