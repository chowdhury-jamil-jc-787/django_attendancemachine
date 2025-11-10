from django.contrib import admin
from .models import MealPayment

@admin.register(MealPayment)
class MealPaymentAdmin(admin.ModelAdmin):
    list_display = ("date", "amount", "currency", "method", "status", "paid_by", "paid_at")
    list_filter = ("status", "method", "currency")
    search_fields = ("date", "transaction_id", "invoice_number", "paid_by__username")
