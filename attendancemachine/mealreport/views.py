from datetime import date as date_cls, timedelta
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.views import APIView

from meal.models import CookRecord, Meal, MealOverride, MealOptOut
from leave.models import Leave
from .models import MealPayment
from .serializers import MealPaymentSerializer, DailyReportRowSerializer

User = get_user_model()


# ------------------------------
# Helper
# ------------------------------
def _is_admin(user):
    """Only 'frahman' can mark or delete payments."""
    return getattr(user, "username", "").lower() == "frahman"


# ------------------------------
# Core Generator (Actual Data)
# ------------------------------
def generate_cook_record(for_date: date_cls):
    """
    Generate a CookRecord entry for the given date using:
    - Override meal (if exists)
    - Weekly meal (if exists)
    - Live counts (users, leaves, opt-outs)
    """

    weekday = for_date.weekday()  # 0=Mon, 6=Sun
    if weekday in (5, 6):  # weekend skip
        return None

    # --- 1️⃣ Meal source ---
    override = MealOverride.objects.filter(date=for_date).first()
    if override:
        source_type = "override"
        item = override.item
        price = override.price
        notes = f"Override: {override.notes or override.item}"
    else:
        # fallback weekly menu from meal table (day field)
        weekday_name = [
            "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
        ][weekday]
        weekly_meal = Meal.objects.filter(day__iexact=weekday_name).first()
        if weekly_meal:
            source_type = "weekly"
            item = weekly_meal.item
            price = weekly_meal.price
            notes = "Weekly menu"
        else:
            # fallback default
            source_type = "weekly"
            item = "Regular Meal"
            price = 70
            notes = "Default menu"

    # --- 2️⃣ Total users ---
    all_users = User.objects.filter(is_active=True).exclude(username__iexact="frahman")
    total_users = all_users.count()

    # --- 3️⃣ Leave users ---
    leave_users = Leave.objects.filter(
        date__contains=for_date.isoformat(),
        status__in=["approved", "pending"]
    ).exclude(user__username__iexact="frahman").values_list("user_id", flat=True)

    # --- 4️⃣ Opt-out users ---
    optout_users = MealOptOut.objects.filter(
        active=True, date=for_date
    ).exclude(user__username__iexact="frahman").values_list("user_id", flat=True)

    # --- 5️⃣ Eaters list (store user_id array) ---
    leave_set = set(leave_users)
    optout_set = set(optout_users)

    eaters = [
        u.id for u in all_users
        if u.id not in leave_set and u.id not in optout_set
    ]

    total_eaters = len(eaters)
    on_leave = len(leave_set)

    # --- 6️⃣ Save CookRecord ---
    obj, _ = CookRecord.objects.update_or_create(
        date=for_date,
        defaults={
            "source": source_type,
            "item": item,
            "price": price,
            "present_count": total_users,
            "on_leave_count": on_leave,
            "eaters_count": total_eaters,
            "notes": notes,
            "is_finalized": True,
            "finalized_at": timezone.now(),
            "eaters": eaters,  # ✅ save user IDs here
        },
    )
    return obj


# ------------------------------
# Views
# ------------------------------
class MealDailyReportListView(APIView):
    """
    GET /api/mealreport/daily/
    → Auto-generates upcoming 7 weekdays (Mon–Fri).
    Optional: &include_details=1
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qp = request.query_params
        date_str = qp.get("date")
        start = qp.get("start")
        end = qp.get("end")
        include_details = str(qp.get("include_details", "")).lower() in ("1", "true", "t", "yes", "y")

        today = date_cls.today()
        weekday = today.weekday()

        # If today is Sat/Sun → move to next Monday
        if weekday == 5:
            today = today + timedelta(days=2)
        elif weekday == 6:
            today = today + timedelta(days=1)

        # --- Lazy auto-create 7 weekdays ahead ---
        generated_dates = []
        for i in range(7):
            day = today + timedelta(days=i)
            if day.weekday() < 5:
                generate_cook_record(day)
                generated_dates.append(day)

        # --- Filter queryset ---
        if not (date_str or start or end):
            qs = CookRecord.objects.filter(date__in=generated_dates).order_by("date")
        else:
            qs = CookRecord.objects.all().order_by("-date")
            if date_str:
                d = parse_date(date_str)
                if not d:
                    return Response({"error": "Invalid date format."}, status=400)
                qs = qs.filter(date=d)
            else:
                if start:
                    sd = parse_date(start)
                    if not sd:
                        return Response({"error": "Invalid start date."}, status=400)
                    qs = qs.filter(date__gte=sd)
                if end:
                    ed = parse_date(end)
                    if not ed:
                        return Response({"error": "Invalid end date."}, status=400)
                    qs = qs.filter(date__lte=ed)

        # --- Combine with payments ---
        payments = {p.date: p for p in MealPayment.objects.filter(date__in=qs.values_list("date", flat=True))}
        out = []

        for rec in qs:
            pay = payments.get(rec.date)
            row = DailyReportRowSerializer.from_record(rec, pay, include_details=include_details)
            out.append(row)

        if not out:
            return Response({"message": "No meal data available."}, status=200)

        return Response(out, status=200)


class MealDailyReportDetailView(APIView):
    """
    GET /api/mealreport/daily/<date>/
    → Returns single day summary with per-user breakdown.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, date):
        d = parse_date(date)
        if not d:
            return Response({"error": "Invalid date."}, status=400)

        if d.weekday() in (5, 6):
            return Response({"message": "Weekend — no meal scheduled."}, status=200)

        rec = CookRecord.objects.filter(date=d).first()
        if not rec:
            rec = generate_cook_record(d)

        pay = MealPayment.objects.filter(date=d).first()
        data = DailyReportRowSerializer.from_record(rec, pay, include_details=True)
        return Response(data, status=200)


class MealDailyUsersView(APIView):
    """
    GET /api/mealreport/daily/<date>/users/
    → Returns eater users for that date (username, name, email)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, date):
        d = parse_date(date)
        if not d:
            return Response({"error": "Invalid date."}, status=400)

        rec = CookRecord.objects.filter(date=d).first()
        if not rec:
            return Response({"error": "CookRecord not found for this date."}, status=404)

        user_qs = User.objects.filter(id__in=rec.eaters).values(
            "id", "username", "first_name", "last_name", "email"
        )

        data = {
            "date": rec.date,
            "meal": {
                "source": rec.source,
                "item": rec.item,
                "price": rec.price,
                "is_override": (rec.source == "override"),
            },
            "eaters": list(user_qs),
        }

        return Response(data, status=200)


class MealPaymentView(APIView):
    """
    POST   /api/mealreport/payment/   -> create/update payment (frahman only)
    DELETE /api/mealreport/payment/   -> delete payment (frahman only)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _is_admin(request.user):
            return Response({"error": "Only frahman can mark payments."}, status=403)

        date_str = request.data.get("date")
        d = parse_date(date_str) if date_str else None
        if not d:
            return Response({"error": "date is required (YYYY-MM-DD)."}, status=400)

        if d.weekday() in (5, 6):
            return Response({"error": "Payments not applicable for weekends."}, status=400)

        rec = CookRecord.objects.filter(date=d).first()
        if not rec:
            rec = generate_cook_record(d)
            if not rec:
                return Response({"error": "Could not generate CookRecord for this date."}, status=404)

        # 🚫 Prevent payment if override meal
        if rec.source == "override":
            return Response({"error": "Cannot record payment for override meal."}, status=403)

        payload = {
            "date": d,
            "amount": request.data.get("amount"),
            "currency": request.data.get("currency", "BDT"),
            "method": request.data.get("method", "bkash"),
            "status": request.data.get("status", "success"),
            "transaction_id": request.data.get("transaction_id", f"bkash_txn_{d.isoformat()}"),
            "invoice_number": request.data.get("invoice_number", f"Meal-{d.isoformat()}"),
            "response_data": request.data.get("response_data"),
            "remarks": request.data.get("remarks", "Paid full"),
            "paid_by": request.user.id,
        }

        obj, created = MealPayment.objects.update_or_create(date=d, defaults=payload)
        ser = MealPaymentSerializer(obj)
        return Response(
            {"message": "Payment recorded" if created else "Payment updated", "payment": ser.data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    def delete(self, request):
        if not _is_admin(request.user):
            return Response({"error": "Only frahman can delete payments."}, status=403)

        date_str = request.data.get("date")
        d = parse_date(date_str) if date_str else None
        if not d:
            return Response({"error": "date is required (YYYY-MM-DD)."}, status=400)

        deleted, _ = MealPayment.objects.filter(date=d).delete()
        if deleted:
            return Response({"message": "Payment deleted."}, status=200)
        return Response({"message": "No payment found for that date."}, status=404)
