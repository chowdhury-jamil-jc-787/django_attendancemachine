from django.db import models
from django.conf import settings

class MealPayment(models.Model):
    STATUS_CHOICES = [
        ("initiated", "Initiated"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
        ("void", "Void"),
        ("partial", "Partial"),
    ]

    date = models.DateField(unique=True, db_index=True)  # meal day (links to CookRecord.date)
    amount = models.FloatField()
    currency = models.CharField(max_length=8, default="BDT")
    method = models.CharField(max_length=50, default="bkash")  # bkash, bkash_qr, etc.
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="success")

    # Optional but recommended for bkash reconciliation
    transaction_id = models.CharField(max_length=100, blank=True, null=True)  # bKash paymentID/transaction id
    invoice_number = models.CharField(max_length=100, blank=True, null=True)  # your internal invoice/ref
    response_data = models.JSONField(blank=True, null=True)  # raw gateway response (audit/debug)

    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="meal_payments"
    )
    paid_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["method"]),
        ]

    def __str__(self):
        who = getattr(self.paid_by, "username", "system")
        return f"{self.date} – {self.amount} {self.currency} via {self.method} by {who}"
