from datetime import date as date_cls, timedelta
from django.db import connection
from django.db.models import Q
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


# -----------------------------------------
# Helper
# -----------------------------------------
def _is_admin(user):
    """Only 'frahman' can mark or delete payments."""
    return getattr(user, "username", "").lower() == "frahman"


# -----------------------------------------
# Core Generator
# -----------------------------------------
def generate_cook_record(for_date: date_cls):
    """
    Generate CookRecord for a given date, using raw SQL for opt-out count.
    """
    weekday = for_date.weekday()  # 0=Mon, 6=Sun
    if weekday in (5, 6):  # weekend skip
        return None

    # --- 1️⃣ Determine source (override / weekly / default) ---
    override = MealOverride.objects.filter(date=for_date).first()
    if override:
        source_type = "override"
        item = override.item
        price = override.price
        notes = f"Override: {override.notes or override.item}"
    else:
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
            source_type = "weekly"
            item = "Regular Meal"
            price = 70
            notes = "Default menu"

    # --- 2️⃣ Total active users (excluding admin) ---
    all_users = User.objects.filter(is_active=True).exclude(username__iexact="frahman")
    total_users = all_users.count()

    # --- 3️⃣ On leave users ---
    leave_users = Leave.objects.filter(
        date__contains=for_date.isoformat(),
        status__in=["approved", "pending"]
    ).exclude(user__username__iexact="frahman").values_list("user_id", flat=True)

    # --- 4️⃣ Opt-out users using RAW SQL ---
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(DISTINCT user_id)
            FROM meal_mealoptout
            WHERE active = 1
              AND (
                  (scope = 'date' AND DATE(`date`) = %s)
                  OR (scope = 'range' AND %s BETWEEN DATE(`start_date`) AND DATE(`end_date`))
              )
              AND user_id NOT IN (
                  SELECT id FROM auth_user WHERE LOWER(username) = 'frahman'
              );
        """, [for_date, for_date])
        opt_out_count = cursor.fetchone()[0] or 0

    # --- 5️⃣ Build sets for eater calculation ---
    leave_set = set(leave_users)

    # Get list of opt-out users (not just count)
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT DISTINCT user_id
            FROM meal_mealoptout
            WHERE active = 1
              AND (
                  (scope = 'date' AND DATE(`date`) = %s)
                  OR (scope = 'range' AND %s BETWEEN DATE(`start_date`) AND DATE(`end_date`))
              )
              AND user_id NOT IN (
                  SELECT id FROM auth_user WHERE LOWER(username) = 'frahman'
              );
        """, [for_date, for_date])
        optout_users = [row[0] for row in cursor.fetchall()]

    optout_set = set(optout_users)

    eaters = [u.id for u in all_users if u.id not in leave_set and u.id not in optout_set]

    # --- 6️⃣ Save / update CookRecord ---
    obj, _ = CookRecord.objects.update_or_create(
        date=for_date,
        defaults={
            "source": source_type,
            "item": item,
            "price": price,
            "present_count": total_users,
            "on_leave_count": len(leave_set),
            "opt_out_count": opt_out_count,
            "eaters_count": len(eaters),
            "notes": notes,
            "is_finalized": True,
            "finalized_at": timezone.now(),
            "eaters": eaters,
        },
    )

    return obj


# -----------------------------------------
# Daily Report List
# -----------------------------------------
class MealDailyReportListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qp = request.query_params
        date_str = qp.get("date")
        start = qp.get("start")
        end = qp.get("end")
        include_details = str(qp.get("include_details", "")).lower() in ("1", "true", "yes", "y")

        today = date_cls.today()
        weekday = today.weekday()
        if weekday == 5:
            today += timedelta(days=2)
        elif weekday == 6:
            today += timedelta(days=1)

        # Generate next 7 weekdays
        generated_dates = []
        for i in range(7):
            d = today + timedelta(days=i)
            if d.weekday() < 5:
                generate_cook_record(d)
                generated_dates.append(d)

        # Filter queryset
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

        payments = {
            p.date: p for p in MealPayment.objects.filter(date__in=qs.values_list("date", flat=True))
        }

        out = []
        for rec in qs:
            pay = payments.get(rec.date)
            row = DailyReportRowSerializer.from_record(rec, pay, include_details=include_details)
            out.append(row)

        return Response(out, status=200)


# -----------------------------------------
# Daily Detail
# -----------------------------------------
class MealDailyReportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, date):
        d = parse_date(date)
        if not d:
            return Response({"error": "Invalid date."}, status=400)

        if d.weekday() in (5, 6):
            return Response({"message": "Weekend — no meal scheduled."}, status=200)

        rec = CookRecord.objects.filter(date=d).first() or generate_cook_record(d)
        pay = MealPayment.objects.filter(date=d).first()
        data = DailyReportRowSerializer.from_record(rec, pay, include_details=True)
        return Response(data, status=200)


# -----------------------------------------
# Users (Eaters)
# -----------------------------------------
class MealDailyUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, date):
        d = parse_date(date)
        if not d:
            return Response({"error": "Invalid date."}, status=400)

        rec = CookRecord.objects.filter(date=d).first()
        if not rec:
            return Response({"error": "CookRecord not found."}, status=404)

        eaters = User.objects.filter(id__in=rec.eaters).values(
            "id", "username", "first_name", "last_name", "email"
        )
        return Response(
            {
                "date": rec.date,
                "meal": {"source": rec.source, "item": rec.item, "price": rec.price},
                "eaters": list(eaters),
            },
            status=200,
        )


# -----------------------------------------
# Absentees (Opt-Outs + Leave)
# -----------------------------------------
class MealDailyAbsenteesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, date):
        d = parse_date(date)
        if not d:
            return Response({"error": "Invalid date."}, status=400)

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT user_id, reason
                FROM meal_mealoptout
                WHERE active = 1
                  AND (
                      (scope = 'date' AND DATE(`date`) = %s)
                      OR (scope = 'range' AND %s BETWEEN DATE(`start_date`) AND DATE(`end_date`))
                  );
            """, [d, d])
            opt_out_rows = cursor.fetchall()

        opt_out_users = []
        for user_id, reason in opt_out_rows:
            u = User.objects.filter(id=user_id).first()
            if u:
                opt_out_users.append({
                    "id": u.id,
                    "username": u.username,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "email": u.email,
                    "reason": reason,
                })

        leaves = Leave.objects.filter(date__contains=d.isoformat()).select_related("user")
        leave_users = [
            {
                "id": l.user.id,
                "username": l.user.username,
                "first_name": l.user.first_name,
                "last_name": l.user.last_name,
                "email": l.user.email,
                "reason": getattr(l, "reason", "N/A"),
            }
            for l in leaves
        ]

        return Response({"date": d, "opt_outs": opt_out_users, "on_leave": leave_users}, status=200)


# -----------------------------------------
# Payment
# -----------------------------------------
class MealPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _is_admin(request.user):
            return Response({"error": "Only frahman can mark payments."}, status=403)

        date_str = request.data.get("date")
        d = parse_date(date_str) if date_str else None
        if not d:
            return Response({"error": "date is required (YYYY-MM-DD)."}, status=400)

        rec = CookRecord.objects.filter(date=d).first() or generate_cook_record(d)
        if not rec:
            return Response({"error": "No CookRecord found."}, status=404)

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
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
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


# -----------------------------------------
# Opt-out Summary for 7 days
# -----------------------------------------
class MealOptOutSummaryView(APIView):
    """
    GET /api/mealreport/optouts/
    → Returns opt-out counts for the next 7 weekdays.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = date_cls.today()
        weekday = today.weekday()
        if weekday == 5:
            today += timedelta(days=2)
        elif weekday == 6:
            today += timedelta(days=1)

        dates = [today + timedelta(days=i) for i in range(7) if (today + timedelta(days=i)).weekday() < 5]

        results = []
        with connection.cursor() as cursor:
            for d in dates:
                cursor.execute("""
                    SELECT COUNT(DISTINCT user_id)
                    FROM meal_mealoptout
                    WHERE active = 1
                      AND (
                          (scope = 'date' AND DATE(`date`) = %s)
                          OR (scope = 'range' AND %s BETWEEN DATE(`start_date`) AND DATE(`end_date`))
                      )
                      AND user_id NOT IN (
                          SELECT id FROM auth_user WHERE LOWER(username) = 'frahman'
                      );
                """, [d, d])
                count = cursor.fetchone()[0]
                results.append({"date": str(d), "opt_out_count": int(count or 0)})

        return Response(results)
