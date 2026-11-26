
from django.conf import settings
from django.db import models


class HomeOffice(models.Model):
    """
    Home Office assignment for an employee.

    Only authorized administrators should create,
    update, or cancel Home Office assignments.

    Original attendance machine logs are not modified.
    """

    # Employee receiving Home Office
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="home_office_records"
    )

    # Date of Home Office
    date = models.DateField(
        db_index=True
    )

    # Admin remarks / reason
    reason = models.TextField(
        blank=True,
        default=""
    )

    # Administrator who approved Home Office
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_office_approvals"
    )

    # Record timestamps
    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    # Cancellation details
    cancelled_at = models.DateTimeField(
        null=True,
        blank=True
    )

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="home_office_cancellations"
    )

    cancellation_reason = models.TextField(
        blank=True,
        default=""
    )

    class Meta:
        ordering = ["-date", "-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                name="unique_employee_home_office_date"
            )
        ]

        indexes = [
            models.Index(
                fields=["date", "cancelled_at"],
                name="ho_date_cancel_idx"
            )
        ]

        verbose_name = "Home Office"
        verbose_name_plural = "Home Office Records"

    @property
    def is_active(self):
        """
        Active Home Office if not cancelled.
        """
        return self.cancelled_at is None

    @property
    def attendance_status(self):
        """
        Return Home Office attendance status.
        """
        if self.is_active:
            return "home_office"

        return "cancelled"

    def __str__(self):
        return (
            f"{self.employee.username} - "
            f"{self.date} - "
            f"{self.attendance_status}"
        )
