
"""Employee attendance monitoring and administrative Home Office APIs.

Attendance reporting uses existing Django Users, Profiles, Leave records,
and the attendance_logs database. Home Office approvals live in a separate
Django model; original machine punches and leave records are never edited.

Requires the HomeOffice model and its migration from the preceding step.
"""

from collections import defaultdict
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from leave.models import Leave

from .models import HomeOffice


BD_TZ = ZoneInfo("Asia/Dhaka")

MAX_DAYS = 366
MAX_PAGE_SIZE = 200

LEAVE_STATUSES = {
    "approved_leave",
    "approved_half_day_leave",
    "pending_leave",
    "pending_half_day_leave",
    "rejected_leave",
    "rejected_half_day_leave",
    "cancel_pending_leave",
    "cancel_pending_half_day_leave",
}

ROW_STATUSES = {
    "present",
    "absent_no_leave",
    "home_office",
    "not_confirmed",
} | LEAVE_STATUSES

FILTER_STATUSES = ROW_STATUSES | {
    "all",
    "on_leave",
    "not_present",
    "needs_review",
    "absent",
}

SORT_FIELDS = {
    "date",
    "emp_code",
    "name",
    "status",
    "first_punch",
}


def _parse_date(value):
    try:
        parsed = date_cls.fromisoformat(value)

        if parsed.isoformat() != value:
            raise ValueError

        return parsed

    except (TypeError, ValueError):
        raise ValueError(
            "Dates must use YYYY-MM-DD format."
        )


def _positive_int(value, default, maximum=None):
    if value is None or value == "":
        return default

    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            "Pagination values must be positive integers."
        )

    if number < 1 or (
        maximum is not None and number > maximum
    ):
        raise ValueError(
            "Pagination values must be at least 1"
            + (
                f" and at most {maximum}."
                if maximum
                else "."
            )
        )

    return number


def _fmt_time(value):
    if value is None:
        return None

    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")

    text = str(value)

    try:
        return datetime.fromisoformat(
            text
        ).strftime("%H:%M:%S")

    except ValueError:
        return text[-8:]


def _punch_span(first, last, count):
    """
    Calculate first-to-last punch span.

    This is not net paid working time.
    Short or duplicate punches require review.
    """

    if (
        count < 2
        or not isinstance(first, datetime)
        or not isinstance(last, datetime)
    ):
        return None, True

    seconds = int(
        (last - first).total_seconds()
    )

    if seconds < 60:
        return None, True

    hours, remainder = divmod(seconds, 3600)

    return (
        f"{hours:02d}:{remainder // 60:02d}",
        False,
    )


def _leave_status(leaves):
    """
    Determine the most appropriate leave status
    for an employee with no attendance punch.
    """

    approved = [
        leave
        for leave in leaves
        if (
            leave.status == "approved"
            or (
                leave.status == "cancel_pending"
                and leave.is_approved
            )
        )
    ]

    approved_types = {
        leave.leave_type
        for leave in approved
    }

    # Full-day leave or two approved half-days.
    full_coverage = (
        "full_day" in approved_types
        or {
            "1st_half",
            "2nd_half",
        } <= approved_types
    )

    if full_coverage:
        if any(
            leave.status == "approved"
            for leave in approved
        ):
            return "approved_leave"

        return "cancel_pending_leave"

    # Only part of the day is covered.
    if approved:
        if any(
            leave.status == "approved"
            for leave in approved
        ):
            return "approved_half_day_leave"

        return "cancel_pending_half_day_leave"

    # No approved leave: distinguish applications.
    for state in (
        "pending",
        "cancel_pending",
        "rejected",
    ):
        group = [
            leave
            for leave in leaves
            if leave.status == state
        ]

        if group:
            is_full = any(
                leave.leave_type == "full_day"
                for leave in group
            )

            suffix = (
                "leave"
                if is_full
                else "half_day_leave"
            )

            return f"{state}_{suffix}"

    return "absent_no_leave"


def _matches_status(record, selected):
    if not selected or "all" in selected:
        return True

    current = record["status"]

    for wanted in selected:
        if current == wanted:
            return True

        if (
            wanted == "absent"
            and current == "absent_no_leave"
        ):
            return True

        if (
            wanted == "on_leave"
            and current in LEAVE_STATUSES
        ):
            return True

        if (
            wanted == "not_present"
            and current != "present"
        ):
            return True

        if (
            wanted == "needs_review"
            and record["needs_review"]
        ):
            return True

    return False


def _counts(rows, employee_count):
    result = {
        "total_employees": employee_count,
        "present": 0,
        "absent_no_leave": 0,
        "home_office": 0,
        "leave_applications": 0,
        "not_confirmed": 0,
        "needs_review": 0,
        "total_records": len(rows),
        "status_counts": {},
    }

    for row in rows:
        key = row["status"]

        result["status_counts"][key] = (
            result["status_counts"].get(key, 0) + 1
        )

        if key in LEAVE_STATUSES:
            result["leave_applications"] += 1

        elif key in result:
            result[key] += 1

        if row["needs_review"]:
            result["needs_review"] += 1

    return result


def _is_attendance_admin(user):
    """Keep the existing frahman/staff/superuser policy for both APIs."""
    return bool(
        user.is_authenticated
        and (
            user.is_staff
            or user.is_superuser
            or user.username == "frahman"
        )
    )


def _serialize_home_office(record):
    """API-safe Home Office details, including who assigned/cancelled it."""
    employee = record.employee
    return {
        "id": record.id,
        "employee_id": record.employee_id,
        "emp_code": str(employee.profile.emp_code or "").strip(),
        "employee_name": employee.get_full_name().strip() or employee.username,
        "date": record.date.isoformat(),
        "status": "home_office" if record.is_active else "cancelled",
        "reason": record.reason,
        "assigned_by": (
            {
                "id": record.assigned_by_id,
                "username": record.assigned_by.username,
            }
            if record.assigned_by_id else None
        ),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "cancelled_at": (
            record.cancelled_at.isoformat()
            if record.cancelled_at else None
        ),
        "cancelled_by": (
            {
                "id": record.cancelled_by_id,
                "username": record.cancelled_by.username,
            }
            if record.cancelled_by_id else None
        ),
        "cancellation_reason": record.cancellation_reason,
        "is_active": record.is_active,
    }


def _validate_reason(value, field_name="reason"):
    """Avoid storing huge or non-text notes."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    value = value.strip()
    if len(value) > 2000:
        raise ValueError(f"{field_name} cannot exceed 2000 characters.")
    return value


def _find_employee_from_payload(payload):
    """Find the same employees eligible for the existing attendance report."""
    employee_id = payload.get("employee_id")
    emp_code = payload.get("emp_code")

    if employee_id in (None, "") and emp_code in (None, ""):
        raise ValueError("Provide employee_id or emp_code.")

    queryset = (
        User.objects.filter(is_active=True, profile__emp_code__isnull=False)
        .exclude(profile__emp_code="")
        .select_related("profile")
    )

    if employee_id not in (None, ""):
        try:
            employee_id = int(employee_id)
            if employee_id < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("employee_id must be a positive integer.")
        queryset = queryset.filter(id=employee_id)

    if emp_code not in (None, ""):
        if not isinstance(emp_code, (str, int)):
            raise ValueError("emp_code must be a string or number.")
        queryset = queryset.filter(profile__emp_code=str(emp_code).strip())

    employee = queryset.first()
    if employee is None:
        return None
    if str(employee.profile.emp_code).strip() == "00":
        return None
    return employee


def _validate_home_office_eligibility(employee, day):
    """
    Enforce the same absent_no_leave rules used by AttendanceMonitorView:
    a registered employee, an office-open day, no punches, and no active
    (including pending/rejected) leave application for that date.

    Raises ValueError with an explanation if the date isn't eligible.
    """
    day_text = day.isoformat()
    next_day_text = (day + timedelta(days=1)).isoformat()
    employee_code = str(employee.profile.emp_code).strip()

    # Original report checks for any non-cancelled leave application.
    leave_entries = Leave.objects.filter(user=employee).exclude(
        status="cancelled"
    ).only("date")
    if any(
        day_text in {str(value) for value in (entry.date or [])}
        for entry in leave_entries
    ):
        raise ValueError(
            "The employee already has a leave application for this date."
        )

    # Match the report's office-opening calculation: only punches from
    # active users with registered employee codes confirm an open day.
    known_codes = {
        str(value).strip()
        for value in (
            User.objects.filter(
                is_active=True,
                profile__emp_code__isnull=False,
            )
            .exclude(profile__emp_code="")
            .values_list("profile__emp_code", flat=True)
        )
    }

    with connections["logs"].cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT user_id
            FROM attendance_logs
            WHERE timestamp >= %s AND timestamp < %s
            """,
            [day_text, next_day_text],
        )
        punched_codes = {
            str(row[0]).strip()
            for row in cursor.fetchall()
            if row[0] is not None
        }

    if employee_code in punched_codes:
        raise ValueError(
            "This employee already has a machine punch on that date."
        )

    if not (punched_codes & known_codes):
        raise ValueError(
            "Office attendance is not confirmed for this date; "
            "the employee is not classified as absent_no_leave."
        )


class AttendanceMonitorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # --------------------------------------
        # 1. ADMIN ACCESS
        # --------------------------------------
        user = request.user

        is_admin = _is_attendance_admin(user)

        if not is_admin:
            return Response(
                {
                    "success": False,
                    "error": (
                        "Administrator access required."
                    ),
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        query = request.query_params

        date_text = query.get("date")
        start_text = query.get("start_date")
        end_text = query.get("end_date")

        # --------------------------------------
        # 2. VALIDATE FILTERS
        # --------------------------------------
        try:
            if date_text and (
                start_text or end_text
            ):
                raise ValueError(
                    "Use either date OR "
                    "start_date and end_date."
                )

            if bool(start_text) != bool(end_text):
                raise ValueError(
                    "Both start_date and end_date "
                    "are required for a range."
                )

            if date_text:
                start_day = _parse_date(date_text)
                end_day = start_day

            elif start_text:
                start_day = _parse_date(start_text)
                end_day = _parse_date(end_text)

            else:
                start_day = timezone.localdate(
                    timezone=BD_TZ
                )
                end_day = start_day

            if start_day > end_day:
                raise ValueError(
                    "end_date cannot be earlier "
                    "than start_date."
                )

            total_days = (
                end_day - start_day
            ).days + 1

            if total_days > MAX_DAYS:
                raise ValueError(
                    f"Date range cannot exceed "
                    f"{MAX_DAYS} days."
                )

            page = _positive_int(
                query.get("page"),
                1,
            )

            per_page = _positive_int(
                query.get("per_page"),
                50,
                MAX_PAGE_SIZE,
            )

            sort = query.get(
                "sort", "date"
            ).strip().lower()

            order = query.get(
                "order", "desc"
            ).strip().lower()

            if sort not in SORT_FIELDS:
                raise ValueError(
                    "Invalid sort. Use: "
                    + ", ".join(sorted(SORT_FIELDS))
                )

            if order not in {"asc", "desc"}:
                raise ValueError(
                    "order must be asc or desc."
                )

            requested_statuses = {
                item.strip().lower()
                for item in query.get(
                    "status", "all"
                ).split(",")
                if item.strip()
            }

            invalid_statuses = (
                requested_statuses
                - FILTER_STATUSES
            )

            if invalid_statuses:
                raise ValueError(
                    "Invalid status: "
                    + ", ".join(
                        sorted(invalid_statuses)
                    )
                )

            office_filter = query.get(
                "office_status", "all"
            ).strip().lower()

            if office_filter not in {
                "all",
                "open",
                "not_confirmed",
            }:
                raise ValueError(
                    "office_status must be "
                    "open or not_confirmed."
                )

        except ValueError as exc:
            return Response(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --------------------------------------
        # 3. EMPLOYEE CODE AND SEARCH FILTERS
        # --------------------------------------
        codes_text = query.get(
            "emp_code", ""
        )

        code_filter = {
            value.strip()
            for value in codes_text.split(",")
            if value.strip()
        }

        search = query.get(
            "search", ""
        ).strip().casefold()

        # --------------------------------------
        # 4. LOAD EMPLOYEES
        # --------------------------------------
        try:
            users = list(
                User.objects.filter(
                    is_active=True,
                    profile__emp_code__isnull=False,
                )
                .exclude(
                    profile__emp_code=""
                )
                .select_related("profile")
                .order_by(
                    "first_name",
                    "last_name",
                    "id",
                )
            )

            # Keep all registered codes for
            # office opening detection.
            known_codes = {
                str(
                    u.profile.emp_code
                ).strip()
                for u in users
            }

            # Exclude administrator employee code 00
            # from employee reporting.
            employees = [
                u
                for u in users
                if str(
                    u.profile.emp_code
                ).strip() != "00"
            ]

            # Specific employee codes.
            if code_filter:
                employees = [
                    u
                    for u in employees
                    if str(
                        u.profile.emp_code
                    ).strip() in code_filter
                ]

            # Search by employee code,
            # name, username, or email.
            if search:
                employees = [
                    u
                    for u in employees
                    if (
                        search in str(
                            u.profile.emp_code
                        ).casefold()
                        or search in (
                            u.get_full_name()
                        ).casefold()
                        or search in (
                            u.username
                        ).casefold()
                        or search in (
                            u.email or ""
                        ).casefold()
                    )
                ]

            # --------------------------------------
            # 5. READ ATTENDANCE LOGS
            # --------------------------------------
            next_day = (
                end_day + timedelta(days=1)
            ).isoformat()

            with connections["logs"].cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        user_id,
                        DATE(timestamp),
                        MIN(timestamp),
                        MAX(timestamp),
                        COUNT(*),
                        COUNT(DISTINCT timestamp)
                    FROM attendance_logs
                    WHERE timestamp >= %s
                      AND timestamp < %s
                    GROUP BY
                        user_id,
                        DATE(timestamp)
                    """,
                    [
                        start_day.isoformat(),
                        next_day,
                    ],
                )

                raw_punches = cursor.fetchall()

            # --------------------------------------
            # 6. LOAD LEAVE APPLICATIONS
            # --------------------------------------
            leave_query = Leave.objects.filter(
                user_id__in=[
                    u.id
                    for u in employees
                ]
            ).exclude(
                status="cancelled"
            )

            # Optimize a single-day lookup.
            if start_day == end_day:
                leave_query = leave_query.filter(
                    date__contains=[
                        start_day.isoformat()
                    ]
                )

            leaves = list(
                leave_query.order_by(
                    "-updated_at",
                    "-id",
                )
            )

            # Only load active Home Office approvals for the selected
            # employees and date range. This is one query, not one per row.
            home_offices = list(
                HomeOffice.objects.filter(
                    employee_id__in=[u.id for u in employees],
                    date__range=(start_day, end_day),
                    cancelled_at__isnull=True,
                ).select_related(
                    "employee__profile",
                    "assigned_by",
                    "cancelled_by",
                )
            )

        except DatabaseError:
            return Response(
                {
                    "success": False,
                    "error": (
                        "Unable to load "
                        "attendance data."
                    ),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # --------------------------------------
        # 7. ORGANIZE PUNCH RECORDS
        # --------------------------------------
        punch_map = {}
        office_open_dates = set()

        for (
            raw_code,
            punch_day,
            first,
            last,
            count,
            distinct_count,
        ) in raw_punches:

            code = (
                str(raw_code).strip()
                if raw_code is not None
                else ""
            )

            if code not in known_codes:
                continue

            day_text = (
                punch_day.isoformat()
                if hasattr(
                    punch_day, "isoformat"
                )
                else str(punch_day)
            )

            # Any known registered user punching
            # establishes that the office was open.
            office_open_dates.add(day_text)

            punch_map[
                (code, day_text)
            ] = (
                first,
                last,
                int(count),
                int(distinct_count),
            )

        # --------------------------------------
        # 8. ORGANIZE LEAVE BY EMPLOYEE AND DATE
        # --------------------------------------
        leave_map = defaultdict(list)

        range_start = start_day.isoformat()
        range_end = end_day.isoformat()

        for leave in leaves:
            for day_value in (
                leave.date or []
            ):
                day_text = str(day_value)

                if (
                    range_start
                    <= day_text
                    <= range_end
                ):
                    leave_map[
                        (leave.user_id, day_text)
                    ].append(leave)

        # Active approvals indexed by employee and ISO attendance date.
        home_office_map = {
            (record.employee_id, record.date.isoformat()): record
            for record in home_offices
        }

        # --------------------------------------
        # 9. BUILD THE SELECTED DATE RANGE
        # --------------------------------------
        all_days = [
            (
                start_day + timedelta(
                    days=offset
                )
            ).isoformat()
            for offset in range(total_days)
        ]

        chosen_days = [
            day
            for day in all_days
            if (
                office_filter == "all"
                or (
                    office_filter == "open"
                    and day in office_open_dates
                )
                or (
                    office_filter == "not_confirmed"
                    and day not in office_open_dates
                )
            )
        ]

        # --------------------------------------
        # 10. PREPARE SUMMARIES
        # --------------------------------------
        rows = []
        per_day = defaultdict(list)
        per_employee = {}

        open_day_count = sum(
            day in office_open_dates
            for day in chosen_days
        )

        for employee in employees:
            code = str(
                employee.profile.emp_code
            ).strip()

            name = (
                employee.get_full_name().strip()
                or employee.username
            )

            per_employee[code] = {
                "id": employee.id,
                "emp_code": code,
                "name": name,
                "present": 0,
                "absent_no_leave": 0,
                "home_office": 0,
                "leave_applications": 0,
                "not_confirmed": 0,
                "needs_review": 0,
                "total_days": len(chosen_days),
                "open_days": open_day_count,
            }

        # --------------------------------------
        # 11. CHECK EVERY EMPLOYEE FOR EACH DATE
        # --------------------------------------
        for day_text in chosen_days:

            is_open = (
                day_text in office_open_dates
            )

            for employee in employees:

                code = str(
                    employee.profile.emp_code
                ).strip()

                name = (
                    employee.get_full_name().strip()
                    or employee.username
                )

                raw = punch_map.get(
                    (code, day_text)
                )

                leaves_today = leave_map.get(
                    (employee.id, day_text),
                    [],
                )

                assigned_home_office = home_office_map.get(
                    (employee.id, day_text)
                )

                row = {
                    "id": employee.id,
                    "emp_code": code,
                    "name": name,
                    "email": employee.email,
                    "date": day_text,
                    "office_status": (
                        "open"
                        if is_open
                        else "not_confirmed"
                    ),
                    "status": "not_confirmed",
                    "first_punch": None,
                    "last_punch": None,
                    "punch_count": 0,
                    "distinct_punch_times": 0,
                    "working_hours": None,
                    "needs_review": False,
                    "home_office": None,
                    "leave": [
                        {
                            "id": leave.id,
                            "leave_type": (
                                leave.leave_type
                            ),
                            "reason": leave.reason,
                            "status": leave.status,
                            "is_approved": (
                                leave.is_approved
                            ),
                        }
                        for leave in leaves_today
                    ],
                }

                # CASE A: EMPLOYEE PRESENT
                if raw is not None:
                    (
                        first,
                        last,
                        count,
                        distinct_count,
                    ) = raw

                    span, short_span = (
                        _punch_span(
                            first,
                            last,
                            count,
                        )
                    )

                    row.update(
                        status="present",
                        first_punch=(
                            _fmt_time(first)
                        ),
                        last_punch=(
                            _fmt_time(last)
                        ),
                        punch_count=count,
                        distinct_punch_times=(
                            distinct_count
                        ),
                        working_hours=span,
                        needs_review=(
                            short_span
                            or distinct_count < 2
                        ),
                    )

                # CASE B: LEAVE APPLICATION EXISTS
                elif leaves_today:
                    row["status"] = (
                        _leave_status(
                            leaves_today
                        )
                    )

                # CASE C: APPROVED HOME OFFICE. Punch and leave data
                # take priority if records were added after approval.
                elif assigned_home_office is not None:
                    row["status"] = "home_office"
                    row["home_office"] = _serialize_home_office(
                        assigned_home_office
                    )

                # CASE D: OFFICE OPEN, NO PUNCH, NO LEAVE OR HOME OFFICE
                elif is_open:
                    row["status"] = (
                        "absent_no_leave"
                    )

                # CASE E: OFFICE NOT CONFIRMED
                else:
                    row["status"] = (
                        "not_confirmed"
                    )

                rows.append(row)

                per_day[
                    day_text
                ].append(row)

                employee_counter = (
                    per_employee[code]
                )

                if row["status"] in LEAVE_STATUSES:
                    employee_counter[
                        "leave_applications"
                    ] += 1
                else:
                    employee_counter[
                        row["status"]
                    ] += 1

                if row["needs_review"]:
                    employee_counter[
                        "needs_review"
                    ] += 1

        # --------------------------------------
        # 12. OVERALL REPORT SUMMARY
        # --------------------------------------
        summary = _counts(
            rows,
            len(employees),
        )

        summary["total_days"] = len(
            chosen_days
        )

        summary["open_days"] = (
            open_day_count
        )

        summary["total_employee_days"] = (
            len(rows)
        )

        # --------------------------------------
        # 13. DAILY SUMMARY
        # --------------------------------------
        daily_summary = [
            {
                "date": day,
                "office_status": (
                    "open"
                    if day in office_open_dates
                    else "not_confirmed"
                ),
                **_counts(
                    per_day[day],
                    len(employees),
                ),
            }
            for day in reversed(chosen_days)
        ]

        # --------------------------------------
        # 14. STATUS FILTER
        # --------------------------------------
        filtered = [
            row
            for row in rows
            if _matches_status(
                row,
                requested_statuses,
            )
        ]

        filtered_summary = _counts(
            filtered,
            len(employees),
        )

        # --------------------------------------
        # 15. SORTING
        # --------------------------------------
        # Stable initial ordering by name.
        filtered.sort(
            key=lambda row: (
                row["name"].casefold(),
                row["emp_code"],
            )
        )

        if sort == "emp_code":
            filtered.sort(
                key=lambda row: (
                    (
                        0,
                        int(row["emp_code"]),
                    )
                    if row["emp_code"].isdigit()
                    else (
                        1,
                        row["emp_code"].casefold(),
                    )
                ),
                reverse=order == "desc",
            )

        else:
            filtered.sort(
                key=lambda row: (
                    (row[sort] or "").casefold()
                    if isinstance(
                        row[sort], str
                    )
                    else (
                        row[sort] or ""
                    )
                ),
                reverse=order == "desc",
            )

        # --------------------------------------
        # 16. PAGINATION / INFINITE SCROLL
        # --------------------------------------
        total = len(filtered)

        start_index = (
            page - 1
        ) * per_page

        results = filtered[
            start_index:
            start_index + per_page
        ]

        last_page = (
            total + per_page - 1
        ) // per_page

        def page_url(target):
            parameters = (
                request.query_params.copy()
            )

            parameters["page"] = str(target)
            parameters["per_page"] = str(
                per_page
            )

            return request.build_absolute_uri(
                "?" + parameters.urlencode()
            )

        # --------------------------------------
        # 17. OFFICE STATUS
        # --------------------------------------
        is_single = (
            start_day == end_day
        )

        if not chosen_days:
            office_status = "not_in_filter"

        elif all(
            day in office_open_dates
            for day in chosen_days
        ):
            office_status = "open"

        elif all(
            day not in office_open_dates
            for day in chosen_days
        ):
            office_status = "not_confirmed"

        else:
            office_status = "mixed"

        # --------------------------------------
        # 18. FINAL API RESPONSE
        # --------------------------------------
        return Response(
            {
                "success": True,

                "date": (
                    start_day.isoformat()
                    if is_single
                    else None
                ),

                "start_date": (
                    start_day.isoformat()
                ),

                "end_date": (
                    end_day.isoformat()
                ),

                "office_status": office_status,

                "is_provisional": (
                    end_day >= timezone.localdate(
                        timezone=BD_TZ
                    )
                ),

                "filters": {
                    "emp_code": sorted(
                        code_filter
                    ),
                    "search": query.get(
                        "search", ""
                    ).strip(),
                    "status": sorted(
                        requested_statuses
                        or {"all"}
                    ),
                    "office_status": (
                        office_filter
                    ),
                    "sort": sort,
                    "order": order,
                },

                # Unfiltered status totals within
                # employee/date/office filters.
                "summary": summary,

                # Totals after status filtering.
                "filtered_summary": (
                    filtered_summary
                ),

                # Daily breakdown.
                "daily_summary": daily_summary,

                # Totals grouped by employee.
                "employee_summary": list(
                    per_employee.values()
                ),

                # Infinite-scroll metadata.
                "pagination": {
                    "total": total,
                    "per_page": per_page,
                    "current_page": page,
                    "last_page": last_page,
                    "has_more": (
                        page < last_page
                    ),
                    "next_page_url": (
                        page_url(page + 1)
                        if page < last_page
                        else None
                    ),
                    "prev_page_url": (
                        page_url(page - 1)
                        if page > 1 and last_page
                        else None
                    ),
                },

                # Paginated employee-day records.
                "results": results,

                # Existing response field names
                # retained for compatibility.
                # These contain the current page.
                "employees": results,

                "present": [
                    row
                    for row in results
                    if row["status"] == "present"
                ],

                "absent_no_leave": [
                    row
                    for row in results
                    if row["status"]
                    == "absent_no_leave"
                ],

                # New list; all existing response fields are preserved.
                "home_office": [
                    row
                    for row in results
                    if row["status"] == "home_office"
                ],

                "on_leave": [
                    row
                    for row in results
                    if row["status"]
                    in LEAVE_STATUSES
                ],

                "not_confirmed": [
                    row
                    for row in results
                    if row["status"]
                    == "not_confirmed"
                ],
            },
            status=status.HTTP_200_OK,
        )


class HomeOfficeView(APIView):
    """
    GET /api/attendance-monitor/home-office/
        List approvals with page/per_page, date, employee and status filters.

    POST /api/attendance-monitor/home-office/
        Assign a past/current absent_no_leave employee-day as Home Office.
        Body: {"employee_id": 123, "date": "2026-11-25", "reason": "..."}
        Or use "emp_code" instead of "employee_id".
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_attendance_admin(request.user):
            return Response(
                {"success": False, "error": "Administrator access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        query = request.query_params
        try:
            page = _positive_int(query.get("page"), 1)
            per_page = _positive_int(query.get("per_page"), 50, MAX_PAGE_SIZE)
            date_text = query.get("date")
            start_text = query.get("start_date")
            end_text = query.get("end_date")
            if date_text and (start_text or end_text):
                raise ValueError("Use either date OR start_date and end_date.")
            if bool(start_text) != bool(end_text):
                raise ValueError("Both start_date and end_date are required for a range.")

            selected_date = _parse_date(date_text) if date_text else None
            start_day = _parse_date(start_text) if start_text else None
            end_day = _parse_date(end_text) if end_text else None
            if start_day and start_day > end_day:
                raise ValueError("end_date cannot be earlier than start_date.")

            state = str(query.get("status", "active")).strip().lower()
            if state not in ("active", "cancelled", "all"):
                raise ValueError("status must be active, cancelled or all.")

            employee_id = query.get("employee_id")
            if employee_id not in (None, ""):
                employee_id = _positive_int(employee_id, 1)

            emp_code = str(query.get("emp_code", "")).strip()
        except ValueError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            records = HomeOffice.objects.select_related(
                "employee__profile", "assigned_by", "cancelled_by"
            ).all()
            if state == "active":
                records = records.filter(cancelled_at__isnull=True)
            elif state == "cancelled":
                records = records.filter(cancelled_at__isnull=False)
            if selected_date:
                records = records.filter(date=selected_date)
            elif start_day:
                records = records.filter(date__range=(start_day, end_day))
            if employee_id:
                records = records.filter(employee_id=employee_id)
            if emp_code:
                records = records.filter(employee__profile__emp_code=emp_code)

            count = records.count()
            start_index = (page - 1) * per_page
            entries = list(records[start_index:start_index + per_page])
        except DatabaseError:
            return Response(
                {"success": False, "error": "Unable to load Home Office records."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        last_page = (count + per_page - 1) // per_page

        def page_url(target):
            parameters = request.query_params.copy()
            parameters["page"] = str(target)
            parameters["per_page"] = str(per_page)
            return request.build_absolute_uri("?" + parameters.urlencode())

        return Response(
            {
                "success": True,
                "count": count,
                "results": [_serialize_home_office(record) for record in entries],
                "pagination": {
                    "total": count,
                    "per_page": per_page,
                    "current_page": page,
                    "last_page": last_page,
                    "has_more": page < last_page,
                    "next_page_url": (
                        page_url(page + 1) if page < last_page else None
                    ),
                    "prev_page_url": (
                        page_url(page - 1) if page > 1 and last_page else None
                    ),
                },
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        if not _is_attendance_admin(request.user):
            return Response(
                {"success": False, "error": "Administrator access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            day = _parse_date(request.data.get("date"))
            if day > timezone.localdate(timezone=BD_TZ):
                raise ValueError("Home Office cannot be assigned for a future date.")
            reason = _validate_reason(request.data.get("reason"))
            employee = _find_employee_from_payload(request.data)
        except ValueError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except DatabaseError:
            return Response(
                {"success": False, "error": "Unable to load employee data."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if employee is None:
            return Response(
                {"success": False, "error": "Employee not found or not eligible."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            _validate_home_office_eligibility(employee, day)
        except ValueError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        except DatabaseError:
            return Response(
                {"success": False, "error": "Unable to verify attendance logs."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            with transaction.atomic():
                existing = HomeOffice.objects.select_for_update().filter(
                    employee=employee, date=day
                ).first()
                if existing is not None and existing.cancelled_at is None:
                    return Response(
                        {"success": False, "error": "Home Office is already assigned."},
                        status=status.HTTP_409_CONFLICT,
                    )

                if existing is not None:
                    # Re-enable a previously cancelled assignment. A unique
                    # employee/date constraint prevents duplicate records.
                    existing.reason = reason
                    existing.assigned_by = request.user
                    existing.cancelled_at = None
                    existing.cancelled_by = None
                    existing.cancellation_reason = ""
                    existing.save(update_fields=[
                        "reason", "assigned_by", "cancelled_at",
                        "cancelled_by", "cancellation_reason", "updated_at",
                    ])
                    record = existing
                    http_status = status.HTTP_200_OK
                else:
                    record = HomeOffice.objects.create(
                        employee=employee,
                        date=day,
                        reason=reason,
                        assigned_by=request.user,
                    )
                    http_status = status.HTTP_201_CREATED
        except IntegrityError:
            return Response(
                {"success": False, "error": "Home Office is already assigned."},
                status=status.HTTP_409_CONFLICT,
            )
        except DatabaseError:
            return Response(
                {"success": False, "error": "Unable to save Home Office assignment."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Avoid additional queries in the serialization helper.
        record = HomeOffice.objects.select_related(
            "employee__profile", "assigned_by", "cancelled_by"
        ).get(pk=record.pk)

        return Response(
            {
                "success": True,
                "message": "Home Office assigned successfully.",
                "home_office": _serialize_home_office(record),
            },
            status=http_status,
        )


class HomeOfficeDetailView(APIView):
    """
    DELETE /api/attendance-monitor/home-office/<int:pk>/
    Cancel an approval and return the employee-day to its calculated status.
    Optional JSON body: {"reason": "Assigned in error"}
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if not _is_attendance_admin(request.user):
            return Response(
                {"success": False, "error": "Administrator access required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            cancellation_reason = _validate_reason(
                request.data.get("reason"), "reason"
            )
        except ValueError as exc:
            return Response(
                {"success": False, "error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                record = HomeOffice.objects.select_for_update().filter(
                    pk=pk
                ).first()
                if record is None:
                    return Response(
                        {"success": False, "error": "Home Office record not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                if record.cancelled_at is not None:
                    return Response(
                        {"success": False, "error": "Home Office is already cancelled."},
                        status=status.HTTP_409_CONFLICT,
                    )

                record.cancelled_at = timezone.now()
                record.cancelled_by = request.user
                record.cancellation_reason = cancellation_reason
                record.save(update_fields=[
                    "cancelled_at", "cancelled_by", "cancellation_reason",
                    "updated_at",
                ])
        except DatabaseError:
            return Response(
                {"success": False, "error": "Unable to cancel Home Office assignment."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        record = HomeOffice.objects.select_related(
            "employee__profile", "assigned_by", "cancelled_by"
        ).get(pk=pk)
        return Response(
            {
                "success": True,
                "message": "Home Office cancelled successfully.",
                "home_office": _serialize_home_office(record),
            },
            status=status.HTTP_200_OK,
        )
