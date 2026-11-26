"""Scheduled attendance reminders using existing Firebase device tokens.

Current settings.py cron entry stays valid:
    notifications.services.send_attendance_punch_notifications

The OLD per-punch notification has been removed. This service sends:
- Mon-Fri 07:00, 07:45, 09:00 Asia/Dhaka: missing attendance reminder.
- Mon-Fri 12:00 noon Asia/Dhaka: missing attendance summary to frahman.
- Mon-Fri 8, 9, 10 hours after first punch: workday reminder, ONLY if
  no later distinct punch has been recorded.

The next step adds AttendanceNotificationEvent to notifications/models.py
and its migration. Do NOT start cron until that step is completed.

Important: attendance_logs has timestamp and user_id, but no verified
IN/OUT direction. A second punch is treated conservatively as a possible
checkout: this avoids sending 'leave the office' to someone already gone,
but cannot reliably identify who is physically still at work.
"""

import logging
from collections import defaultdict
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.utils import timezone

from attendance_monitor.models import HomeOffice
from leave.models import Leave

from .firebase import send_push_to_tokens
from .models import AttendanceNotificationEvent, UserDeviceToken


logger = logging.getLogger(__name__)
User = get_user_model()
DHAKA_TZ = ZoneInfo("Asia/Dhaka")

# Cron is scheduled every minute. Short grace windows accommodate minor delays
# but prevent morning reminders from being sent later in the day.
CLOCK_WINDOW = timedelta(minutes=5)
NOON_WINDOW = timedelta(minutes=10)
DURATION_WINDOW = timedelta(minutes=5)
RETRY_DELAY = timedelta(minutes=2)
STALE_CLAIM_DELAY = timedelta(minutes=5)

MORNING_REMINDERS = (
    (
        time(7, 0),
        "missing_0700",
        "Attendance reminder - 7:00 AM",
        "You have not checked in yet. Please record your attendance.",
    ),
    (
        time(7, 45),
        "missing_0745",
        "Attendance reminder - 7:45 AM",
        "Your attendance is still missing. Please check in if you are working today.",
    ),
    (
        time(9, 0),
        "missing_0900",
        "Attendance reminder - 9:00 AM",
        "No attendance has been recorded for you today. Please check in.",
    ),
)

WORKDAY_REMINDERS = (
    (8, "workday_08h"),
    (9, "workday_09h"),
    (10, "workday_10h"),
)


def _dhaka_datetime(value):
    """Machine timestamps are stored as Bangladesh-local wall-clock time."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    if timezone.is_naive(parsed):
        return parsed.replace(tzinfo=DHAKA_TZ)
    return parsed.astimezone(DHAKA_TZ)


def _as_dhaka_now(now):
    now = now if now is not None else timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, DHAKA_TZ)
    return now.astimezone(DHAKA_TZ)


def _in_window(now, when, window):
    return when <= now < when + window


def _name(user):
    return user.get_full_name().strip() or user.username


def _employee_code(user):
    return str(user.profile.emp_code or "").strip()


def _eligible_employee(user):
    """Skip admin accounts and the reserved employee code '00'."""
    return (
        user.is_active
        and not user.is_staff
        and not user.is_superuser
        and user.username.casefold() != "frahman"
        and _employee_code(user) not in ("", "00")
    )


def _load_staff_and_admin():
    registered = list(
        User.objects.filter(
            is_active=True,
            profile__emp_code__isnull=False,
        ).exclude(profile__emp_code="").select_related("profile")
    )
    employees = [u for u in registered if _eligible_employee(u)]
    admin = User.objects.filter(username="frahman", is_active=True).first()
    return employees, admin


def _load_first_punches(day):
    """Read earliest punch and count distinct timestamps per employee.

    The attendance database uses local Dhaka timestamps without a timezone.
    The half-open range permits an index on the timestamp column.
    """
    day_start = datetime.combine(day, time.min)
    next_start = day_start + timedelta(days=1)
    punches = {}

    with connections["logs"].cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, MIN(timestamp), COUNT(DISTINCT timestamp)
            FROM attendance_logs
            WHERE timestamp >= %s AND timestamp < %s
            GROUP BY user_id
            """,
            [day_start, next_start],
        )
        for raw_code, raw_first, count in cursor.fetchall():
            if raw_code is None:
                continue
            code = str(raw_code).strip()
            first = _dhaka_datetime(raw_first)
            if not code or first is None or first.date() != day:
                continue
            # Normalize codes while keeping the earliest timestamp.
            if code not in punches:
                punches[code] = (first, int(count))
            else:
                previous_first, previous_count = punches[code]
                punches[code] = (
                    min(first, previous_first),
                    previous_count + int(count),
                )
    return punches


def _exempt_from_missing_attendance(employees, day):
    """Exclude Home Office and approved full/first-half leave.

    An approved second-half-only leave does not cover the morning.
    Pending and rejected leave also do not count as attendance.
    """
    ids = [user.pk for user in employees]
    if not ids:
        return set()

    exempt_ids = set(
        HomeOffice.objects.filter(
            employee_id__in=ids,
            date=day,
            cancelled_at__isnull=True,
        ).values_list("employee_id", flat=True)
    )

    today_text = day.isoformat()
    leave_qs = Leave.objects.filter(
        user_id__in=ids,
        status__in=("approved", "cancel_pending"),
    ).values("user_id", "date", "leave_type", "status", "is_approved")

    for leave in leave_qs:
        is_approved = (
            leave["status"] == "approved"
            or (leave["status"] == "cancel_pending" and leave["is_approved"])
        )
        dates = leave["date"] or []
        if isinstance(dates, str):
            dates = [dates]
        if (
            is_approved
            and today_text in dates
            and leave["leave_type"] in ("full_day", "1st_half")
        ):
            exempt_ids.add(leave["user_id"])
    return exempt_ids


def _token_map(users):
    result = defaultdict(list)
    ids = [user.pk for user in users]
    if not ids:
        return result
    for user_id, token in UserDeviceToken.objects.filter(
        user_id__in=ids, is_active=True
    ).values_list("user_id", "token"):
        if token and token not in result[user_id]:
            result[user_id].append(token)
    return result


def _claim(user, day, kind, key, now):
    """Reserve an event atomically; retry failed/stale attempts.

    The new AttendanceNotificationEvent model has a unique event_key.
    A process crash after FCM succeeds but before marking sent could cause
    one retry; this is an at-least-once push-delivery limitation.
    """
    with transaction.atomic():
        event, created = (
            AttendanceNotificationEvent.objects.select_for_update()
            .get_or_create(
                event_key=key,
                defaults={
                    "user": user,
                    "attendance_date": day,
                    "event_type": kind,
                    "status": "pending",
                },
            )
        )
        if not created:
            if event.status == "sent":
                return None
            last = event.last_attempt_at
            if event.status == "sending":
                if last and now - last < STALE_CLAIM_DELAY:
                    return None
            elif event.status == "failed":
                if last and now - last < RETRY_DELAY:
                    return None

        event.status = "sending"
        event.attempts += 1
        event.last_attempt_at = now
        event.last_error = ""
        event.save(update_fields=(
            "status", "attempts", "last_attempt_at", "last_error"
        ))
        return event.pk


def _deliver(user, day, kind, title, body, tokens, now, data=None):
    """Deliver one event to every active FCM device token for a user."""
    user_tokens = tokens.get(user.pk, [])
    if not user_tokens:
        return False

    key = f"{day.isoformat()}:{user.pk}:{kind}"
    event_id = _claim(user, day, kind, key, now)
    if event_id is None:
        return False

    payload = {
        "type": kind,
        "date": day.isoformat(),
        "user_id": str(user.pk),
    }
    payload.update(data or {})
    payload = {str(k): str(v) for k, v in payload.items()}

    success = 0
    errors = []
    invalid = []
    try:
        # Firebase multicast supports up to 500 registration tokens per call.
        for index in range(0, len(user_tokens), 500):
            batch = user_tokens[index:index + 500]
            result = send_push_to_tokens(
                tokens=batch,
                title=title,
                body=body,
                data=payload,
            )
            success += int(result.get("success_count") or 0)
            for token, response in zip(batch, result.get("responses") or []):
                if response.success:
                    continue
                detail = str(response.exception)
                errors.append(detail)
                lowered = detail.lower()
                if any(term in lowered for term in (
                    "registration-token-not-registered",
                    "invalid-registration-token",
                    "unregistered",
                )):
                    invalid.append(token)

        if invalid:
            UserDeviceToken.objects.filter(token__in=invalid).update(
                is_active=False
            )

        AttendanceNotificationEvent.objects.filter(pk=event_id).update(
            status="sent" if success else "failed",
            sent_at=timezone.now() if success else None,
            last_error="; ".join(errors)[:1500],
        )
        return success > 0
    except Exception as exc:
        AttendanceNotificationEvent.objects.filter(pk=event_id).update(
            status="failed",
            last_error=str(exc)[:1500],
        )
        logger.exception("Failed to send %s event to user %s", kind, user.pk)
        return False


def _morning_reminders(now, employees, punches, exempt, tokens):
    day = now.date()
    for clock_time, kind, title, message in MORNING_REMINDERS:
        scheduled = datetime.combine(day, clock_time, tzinfo=DHAKA_TZ)
        if not _in_window(now, scheduled, CLOCK_WINDOW):
            continue
        for employee in employees:
            code = _employee_code(employee)
            if code in punches or employee.pk in exempt:
                continue
            _deliver(
                employee, day, kind, title,
                f"{_name(employee)}, {message}",
                tokens, now, {"emp_code": code},
            )


def _noon_summary(now, employees, punches, exempt, tokens, admin):
    if admin is None:
        return
    noon = datetime.combine(now.date(), time(12, 0), tzinfo=DHAKA_TZ)
    if not _in_window(now, noon, NOON_WINDOW):
        return

    missing = [
        u for u in employees
        if _employee_code(u) not in punches and u.pk not in exempt
    ]
    if not missing:
        return

    preview = ", ".join(
        f"{_name(u)} ({_employee_code(u)})" for u in missing[:3]
    )
    if len(missing) > 3:
        preview += f"; and {len(missing) - 3} more"

    _deliver(
        admin, now.date(), "noon_missing_admin",
        "Employees missing attendance - 12:00 PM",
        f"{len(missing)} employee(s) have no attendance today: {preview}"[:250],
        tokens, now,
        {"missing_count": str(len(missing))},
    )


def _working_hours_reminders(now, employees, punches, tokens):
    """Reminder at elapsed 8/9/10 hours if there is no subsequent punch.

    With no IN/OUT field, we cannot prove someone is still in the office.
    If 2+ distinct punches exist, we conservatively suppress the reminder
    because one could be a checkout record.
    """
    for employee in employees:
        code = _employee_code(employee)
        first_and_count = punches.get(code)
        if not first_and_count:
            continue
        first, distinct_punch_count = first_and_count
        if distinct_punch_count != 1:
            continue

        for hours, kind in WORKDAY_REMINDERS:
            deadline = first + timedelta(hours=hours)
            if not _in_window(now, deadline, DURATION_WINDOW):
                continue

            _deliver(
                employee, now.date(), kind,
                f"Workday reminder - {hours} hours",
                (
                    f"{_name(employee)}, {hours} hours have passed since your "
                    f"first attendance at {first.strftime('%I:%M %p')}. "
                    "If you are still at the office, please finish your work "
                    "and check out when appropriate."
                ),
                tokens, now,
                {
                    "emp_code": code,
                    "first_punch": first.isoformat(),
                    "elapsed_hours": str(hours),
                },
            )


def send_attendance_reminders(now=None):
    """Run once each minute (safe to re-run; events are deduplicated)."""
    now = _as_dhaka_now(now)

    # All new attendance notifications are limited to Monday-Friday.
    if now.weekday() >= 5:
        return

    employees, admin = _load_staff_and_admin()
    if not employees:
        return

    punches = _load_first_punches(now.date())
    tokens = _token_map(employees + ([admin] if admin else []))

    # The leave/Home Office query is needed only around scheduled reminders.
    is_scheduled_time = any(
        _in_window(
            now,
            datetime.combine(now.date(), clock, tzinfo=DHAKA_TZ),
            CLOCK_WINDOW,
        )
        for clock, *_ in MORNING_REMINDERS
    ) or _in_window(
        now,
        datetime.combine(now.date(), time(12, 0), tzinfo=DHAKA_TZ),
        NOON_WINDOW,
    )

    if is_scheduled_time:
        exempt = _exempt_from_missing_attendance(employees, now.date())
        _morning_reminders(now, employees, punches, exempt, tokens)
        _noon_summary(now, employees, punches, exempt, tokens, admin)

    _working_hours_reminders(now, employees, punches, tokens)


def send_attendance_punch_notifications():
    """Compatibility wrapper for existing settings.py CRONJOBS entry.

    It no longer sends the old notification for every machine punch.
    """
    return send_attendance_reminders()
