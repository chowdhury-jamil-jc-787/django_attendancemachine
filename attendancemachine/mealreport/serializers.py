from rest_framework import serializers
from django.contrib.auth import get_user_model
from meal.models import CookRecord
from .models import MealPayment

User = get_user_model()


# ------------------------------------------------
# 💳 Meal Payment Serializer
# ------------------------------------------------
class MealPaymentSerializer(serializers.ModelSerializer):
    paid_by_username = serializers.SerializerMethodField()

    class Meta:
        model = MealPayment
        fields = [
            "date",
            "amount",
            "currency",
            "method",
            "status",
            "transaction_id",
            "invoice_number",
            "response_data",
            "paid_by",
            "paid_by_username",
            "paid_at",
            "remarks",
        ]
        read_only_fields = ["paid_at"]

    def get_paid_by_username(self, obj):
        """Return payer username if available"""
        return getattr(obj.paid_by, "username", None)


# ------------------------------------------------
# 📊 Daily Report Serializer (CookRecord + Payment)
# ------------------------------------------------
class DailyReportRowSerializer(serializers.Serializer):
    # --- Summary ---
    date = serializers.DateField()
    meal_source = serializers.CharField()
    item = serializers.CharField()
    price = serializers.FloatField()
    present_count = serializers.IntegerField()
    on_leave_count = serializers.IntegerField()
    opt_out_count = serializers.IntegerField()
    eaters_count = serializers.IntegerField()
    total_amount = serializers.FloatField()
    notes = serializers.CharField(allow_null=True, required=False)

    # --- Payment ---
    paid = serializers.BooleanField()
    payment_method = serializers.CharField(allow_null=True, required=False)
    paid_amount = serializers.FloatField(allow_null=True, required=False)
    paid_by = serializers.CharField(allow_null=True, required=False)
    paid_at = serializers.DateTimeField(allow_null=True, required=False)
    payment_status = serializers.CharField(allow_null=True, required=False)
    transaction_id = serializers.CharField(allow_null=True, required=False)

    # --- Optional details (for future per-user info) ---
    ate_today = serializers.ListField(child=serializers.DictField(), required=False)
    did_not_eat = serializers.ListField(child=serializers.DictField(), required=False)

    @staticmethod
    def from_record(rec: CookRecord, payment: MealPayment = None, include_details: bool = False):
        """
        Convert CookRecord + optional MealPayment into serialized dict.
        """
        # Defensive handling in case total_amount isn’t saved in CookRecord
        total_amount = getattr(rec, "total_amount", None)
        if not total_amount:
            total_amount = (float(rec.price or 0) * int(rec.eaters_count or 0))

        base = {
            # --- Meal Info ---
            "date": rec.date,
            "meal_source": getattr(rec, "source", "weekly"),
            "item": rec.item,
            "price": float(rec.price or 0),
            "present_count": int(rec.present_count or 0),
            "on_leave_count": int(rec.on_leave_count or 0),
            "opt_out_count": int(getattr(rec, "opt_out_count", 0) or 0),
            "eaters_count": int(rec.eaters_count or 0),
            "total_amount": float(total_amount),
            "notes": rec.notes or "",

            # --- Payment Info ---
            "paid": bool(payment and payment.status == "success"),
            "payment_method": getattr(payment, "method", None) if payment else None,
            "paid_amount": float(getattr(payment, "amount", 0)) if payment else None,
            "paid_by": getattr(payment.paid_by, "username", None) if payment else None,
            "paid_at": getattr(payment, "paid_at", None) if payment else None,
            "payment_status": getattr(payment, "status", None) if payment else None,
            "transaction_id": getattr(payment, "transaction_id", None) if payment else None,
        }

        # Optional fields for details (placeholder)
        if include_details:
            base["ate_today"] = []
            base["did_not_eat"] = []

        return DailyReportRowSerializer(base).data
