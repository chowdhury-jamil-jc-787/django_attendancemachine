from datetime import date as date_cls
from django.contrib.auth import get_user_model
from django.db import connections
from django.shortcuts import render
from django.utils.dateparse import parse_date

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from leave.models import Leave

User = get_user_model()

# -----------------------------
# FIXED SEAT LAYOUT (auth_user.id)
# -----------------------------
SEATS = [
    {"id": "teacher", "label": "Faisal Sir", "user_id": 4},  # ALWAYS GREEN

    {"id": "nazim", "label": "Nazim", "user_id": 12},
    {"id": "shohel_top", "label": "Shohel", "user_id": 15},
    {"id": "shafeen", "label": "Shafeen", "user_id": 14},

    {"id": "yunus", "label": "Yunus", "user_id": 11},
    {"id": "muzahid", "label": "Muzahid", "user_id": 13},
    {"id": "tonmoy", "label": "Tonmoy", "user_id": 16},

    {"id": "jamil", "label": "Jamil", "user_id": 1},
    {"id": "imran", "label": "Imran", "user_id": 2},
    {"id": "mahi", "label": "Mahi", "user_id": 3},

    {"id": "monir", "label": "Monir", "user_id": 17},
    {"id": "sohel_bottom", "label": "Sohel", "user_id": 7},
    {"id": "nafisa", "label": "Nafisa", "user_id": 10},
    {"id": "tasfia", "label": "Tasfia", "user_id": 8},

    {"id": "shawon", "label": "Shawon", "user_id": 9},
    {"id": "empty", "label": "Empty", "user_id": None},   # ALWAYS RED
    {"id": "rahad", "label": "Rahad", "user_id": 5},
]


class SeatPlanView(APIView):
    permission_classes = [AllowAny]  # public API

    def get(self, request):
        # -----------------------------
        # 1) Date
        # -----------------------------
        date_str = request.query_params.get("date")
        d = parse_date(date_str) if date_str else date_cls.today()
        if not d:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        iso = d.isoformat()

        # -----------------------------
        # 2) Collect user_ids from seats
        # -----------------------------
        seat_user_ids = [s["user_id"] for s in SEATS if s.get("user_id")]
        seat_user_ids = sorted(set(seat_user_ids))

        # -----------------------------
        # 3) Load users + profiles
        # -----------------------------
        user_to_emp = {}    # user_id -> emp_code
        user_to_name = {}   # user_id -> display name

        users = (
            User.objects
            .select_related("profile")
            .filter(id__in=seat_user_ids)
        )

        for u in users:
            profile = getattr(u, "profile", None)
            emp_code = getattr(profile, "emp_code", None) if profile else None

            if emp_code is not None:
                user_to_emp[u.id] = str(emp_code)

            user_to_name[u.id] = (u.get_full_name() or u.username)

        emp_codes = sorted(set(user_to_emp.values()))

        # -----------------------------
        # 4) Attendance (logs DB)
        # attendance_logs.user_id == emp_code
        # -----------------------------
        present_emp_codes = set()

        if emp_codes:
            placeholders = ",".join(["%s"] * len(emp_codes))
            sql = f"""
                SELECT DISTINCT user_id
                FROM attendance_logs
                WHERE DATE(timestamp) = %s
                  AND user_id IN ({placeholders})
            """
            params = [iso] + emp_codes

            with connections["logs"].cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

            present_emp_codes = {str(r[0]) for r in rows}

        # -----------------------------
        # 5) Approved Leave
        # -----------------------------
        leave_user_ids = set(
            Leave.objects.filter(
                user_id__in=seat_user_ids,
                status="approved",
                date__contains=[iso],
            ).values_list("user_id", flat=True)
        )

        # -----------------------------
        # 6) Build seat results
        # -----------------------------
        results = []
        counts = {"green": 0, "red": 0, "yellow": 0}

        for s in SEATS:
            uid = s.get("user_id")

            # -----------------
            # EMPTY SEAT → RED
            # -----------------
            if uid is None:
                results.append({
                    "id": s["id"],
                    "label": s["label"],
                    "user_id": None,
                    "emp_code": None,
                    "display_name": s["label"],
                    "status": "empty",
                    "color": "red",
                })
                counts["red"] += 1
                continue

            # -----------------
            # FAISAL SIR → ALWAYS GREEN
            # -----------------
            if uid == 4:
                results.append({
                    "id": s["id"],
                    "label": s["label"],
                    "user_id": uid,
                    "emp_code": user_to_emp.get(uid),
                    "display_name": user_to_name.get(uid, s["label"]),
                    "status": "present",
                    "color": "green",
                })
                counts["green"] += 1
                continue

            emp = user_to_emp.get(uid)

            # -----------------
            # NORMAL USERS
            # -----------------
            if emp and emp in present_emp_codes:
                status = "present"
                color = "green"
                counts["green"] += 1

            elif uid in leave_user_ids:
                status = "leave"
                color = "red"
                counts["red"] += 1

            else:
                status = "absent"
                color = "yellow"
                counts["yellow"] += 1

            results.append({
                "id": s["id"],
                "label": s["label"],
                "user_id": uid,
                "emp_code": emp,
                "display_name": user_to_name.get(uid, s["label"]),
                "status": status,
                "color": color,
            })

        return Response({
            "date": iso,
            "summary": counts,
            "seats": results,
        }, status=200)


def seatplan_page(request):
    return render(request, "seatplan.html")
