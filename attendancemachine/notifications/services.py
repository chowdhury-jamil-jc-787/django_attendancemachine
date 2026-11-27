"""Scheduled attendance reminders using existing Firebase device tokens.

Current settings.py cron entry stays valid:
    notifications.services.send_attendance_punch_notifications

This service sends:
- Every day, after each genuine new machine punch: an attendance notification
  showing elapsed time since the first punch of that day.
- Mon-Fri 07:00, 07:45, 09:00 Asia/Dhaka: missing attendance reminder.
- Mon-Fri 11:00 AM Asia/Dhaka: missing attendance summary to frahman.
- Mon-Fri 8, 9, 10 hours after first punch: elapsed-time reminder,
  even when the machine records repeated or later punches.

The AttendanceNotificationEvent model records delivery attempts and prevents
duplicate sends. Keep the existing every-minute scheduled invocation.

Important: attendance_logs has timestamp and user_id, but no verified
IN/OUT direction. Punch messages state elapsed time since the first punch,
NOT verified working duration or physical time spent inside the office.
Nearby duplicate machine rows are grouped into one genuine punch event.
A short settling period permits repeat rows to arrive before notification.
Old punches are never automatically replayed when the service is deployed.
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
ADMIN_MISSING_ALERT_TIME = time(11, 0)
ADMIN_ALERT_WINDOW = timedelta(minutes=10)
DURATION_WINDOW = timedelta(minutes=5)
RETRY_DELAY = timedelta(minutes=2)
STALE_CLAIM_DELAY = timedelta(minutes=5)

# Punch notifications: cron runs every minute. Keep a limited lookback to
# avoid replaying historical punches after a deployment or a long cron outage.
# Your machine commonly writes several timestamps seconds apart for one scan.
PUNCH_EVENT_TYPE = "attendance_punch"
PUNCH_DUPLICATE_GAP = timedelta(seconds=120)
PUNCH_SETTLE_DELAY = timedelta(seconds=75)
PUNCH_RECENT_WINDOW = timedelta(minutes=7)
PUNCH_HISTORY_WINDOW = timedelta(minutes=15)


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


def _deliver(user, day, kind, title, body, tokens, now, data=None, event_key=None):
    """Deliver one event to every active FCM device token for a user."""
    user_tokens = tokens.get(user.pk, [])
    if not user_tokens:
        return False

    key = event_key or f"{day.isoformat()}:{user.pk}:{kind}"
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
                detail = str(response.exception) if response.exception else "Unknown FCM failure"
                errors.append(detail)
                if _is_invalid_fcm_token_error(response.exception):
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


def _is_invalid_fcm_token_error(exception):
    """Recognize token-revocation errors without disabling tokens for outages.

    Firebase Admin SDK versions may expose an exception class such as
    UnregisteredError, or format its message as 'NotRegistered'.
    """
    if exception is None:
        return False

    detail = f"{type(exception).__name__} {exception}".casefold()
    return any(marker in detail for marker in (
        "notregistered",                  # Observed Firebase response
        "unregistered",                   # Firebase Admin SDK exception
        "registration-token-not-registered",
        "invalid-registration-token",
        "invalid registration token",
    ))


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


def _admin_missing_summary(now, employees, punches, exempt, tokens, admin):
    if admin is None:
        return
    scheduled = datetime.combine(now.date(), ADMIN_MISSING_ALERT_TIME, tzinfo=DHAKA_TZ)
    if not _in_window(now, scheduled, ADMIN_ALERT_WINDOW):
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
        # Preserve the existing FCM/event identifier so mobile clients and
        # historical event records remain compatible. Despite this legacy
        # identifier, the alert now runs at 11:00 AM Dhaka time.
        admin, now.date(), "noon_missing_admin",
        "Employees missing attendance - 11:00 AM",
        f"{len(missing)} employee(s) have no attendance today: {preview}"[:250],
        tokens, now,
        {"missing_count": str(len(missing))},
    )


def _working_hours_reminders(now, employees, punches, tokens):
    """Send 8/9/10-hour elapsed-time reminders after the first punch.

    Ignore the number of subsequent/distinct machine punches: duplicate or
    extra records must not silently suppress reminders. Machine logs lack a
    reliable IN/OUT flag, so the message must not imply that the employee is
    still in the office or has worked the entire elapsed period.
    """
    for employee in employees:
        code = _employee_code(employee)
        first_and_count = punches.get(code)
        if not first_and_count:
            continue

        # Preserve the existing punch-loader return shape for compatibility.
        first, _distinct_punch_count = first_and_count

        for hours, kind in WORKDAY_REMINDERS:
            deadline = first + timedelta(hours=hours)
            if not _in_window(now, deadline, DURATION_WINDOW):
                continue

            _deliver(
                employee, now.date(), kind,
                f"Attendance reminder - {hours} hours elapsed",
                (
                    f"{_name(employee)}, {hours} hours have passed since your "
                    f"first attendance punch at {first.strftime('%I:%M %p')}. "
                    "If you are still working, please remember to check out "
                    "when your workday finishes."
                ),
                tokens, now,
                {
                    "emp_code": code,
                    "first_punch": first.isoformat(),
                    "elapsed_hours": str(hours),
                },
            )


def _load_punch_notification_users():
    """Active users with valid machine IDs; includes staff who punch in.

    Morning/workday scheduled reminders continue to use the original
    _eligible_employee restrictions, excluding administrators.
    """
    return list(
        User.objects.filter(is_active=True, profile__emp_code__isnull=False)
        .exclude(profile__emp_code="")
        .select_related("profile")
    )


def _punch_user_by_code(users):
    """Do not expose an employee's attendance to a duplicate emp_code account."""
    lookup = {}
    ambiguous = set()
    for user in users:
        code = _employee_code(user)
        if code in ("", "00"):
            continue
        if code in lookup:
            ambiguous.add(code)
        else:
            lookup[code] = user

    for code in ambiguous:
        lookup.pop(code, None)
        logger.warning("Punch push skipped: duplicate employee code %s", code)
    return lookup


def _load_recent_punches(day, now):
    """Distinct machine timestamps from a short, indexed Dhaka-local range.

    Query uses the machine's naive local-time convention like
    _load_first_punches(). A short lookback prevents historical backfills.
    """
    today_start = datetime.combine(day, time.min)
    next_start = today_start + timedelta(days=1)
    clock = now.astimezone(DHAKA_TZ).replace(tzinfo=None)
    beginning = max(today_start, clock - PUNCH_HISTORY_WINDOW)
    ending = min(next_start, clock + timedelta(microseconds=1))
    if beginning >= ending:
        return {}

    punches = defaultdict(list)
    with connections["logs"].cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT user_id, timestamp
            FROM attendance_logs
            WHERE timestamp >= %s AND timestamp < %s
            ORDER BY user_id, timestamp
            """,
            [beginning, ending],
        )
        for raw_code, raw_time in cursor.fetchall():
            if raw_code is None:
                continue
            code = str(raw_code).strip()
            when = _dhaka_datetime(raw_time)
            if code and when is not None and when.date() == day:
                punches[code].append(when)
    return punches


def _group_punches(times):
    """Return (first, last) for each machine-scan cluster.

    Even exact duplicate timestamps and scans separated by seconds are treated
    as one event; independent scans spaced over 120 seconds are distinct.
    """
    unique = sorted(set(times))
    if not unique:
        return []
    groups = []
    start = end = unique[0]
    for instant in unique[1:]:
        if instant - end <= PUNCH_DUPLICATE_GAP:
            end = instant
        else:
            groups.append((start, end))
            start = end = instant
    groups.append((start, end))
    return groups


def _stable_punch_key(user, day, first):
    """Reuse a nearby event key if machine rows arrived out of order.

    Normally the key is based on the first timestamp in the punch cluster.
    A late-arriving record a few seconds earlier must not generate a second
    event, even if an earlier cron invocation already sent that cluster.
    """
    prefix = f"{day.isoformat()}:{user.pk}:{PUNCH_EVENT_TYPE}:"
    proposed = prefix + first.strftime("%H%M%S")
    previous_keys = AttendanceNotificationEvent.objects.filter(
        user_id=user.pk,
        attendance_date=day,
        event_type=PUNCH_EVENT_TYPE,
        event_key__startswith=prefix,
    ).values_list("event_key", flat=True)

    nearest_key = None
    nearest_gap = PUNCH_DUPLICATE_GAP
    for existing in previous_keys:
        clock = existing[len(prefix):] if existing.startswith(prefix) else ""
        if len(clock) != 6 or not clock.isdecimal():
            continue
        try:
            previous = datetime.combine(
                day, time(int(clock[:2]), int(clock[2:4]), int(clock[4:])),
                tzinfo=DHAKA_TZ,
            )
        except ValueError:
            continue
        gap = abs(first - previous)
        if gap <= nearest_gap:
            nearest_key, nearest_gap = existing, gap
    return nearest_key or proposed


def _elapsed_text(start, instant):
    """Elapsed since first punch; never claim verified time at the office."""
    total_minutes = max(0, int((instant - start).total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _punch_notifications(now, recipients, tokens, day, first_punches):
    """Send newly settled punch clusters only, never old DB history."""
    if not recipients:
        return
    recent = _load_recent_punches(day, now)
    if not recent:
        return

    for code, instants in recent.items():
        user = recipients.get(code)
        day_summary = first_punches.get(code)
        if user is None or not day_summary:
            continue
        if not tokens.get(user.pk):
            continue
        day_first = day_summary[0]
        for punch_start, punch_last in _group_punches(instants):
            age = now - punch_last
            if age < PUNCH_SETTLE_DELAY or age > PUNCH_RECENT_WINDOW:
                continue

            elapsed = _elapsed_text(day_first, punch_start)
            clock = punch_start.strftime("%I:%M %p")
            first_clock = day_first.strftime("%I:%M %p")
            title = "ATPL - Attendance recorded"
            if punch_start == day_first:
                message = (
                    f"{_name(user)}, your first attendance punch was recorded "
                    f"at {clock}. Elapsed since first punch: 0m."
                )
            else:
                message = (
                    f"{_name(user)}, attendance recorded at {clock}. "
                    f"Elapsed since your first punch ({first_clock}): {elapsed}."
                )
            event_key = _stable_punch_key(user, day, punch_start)
            _deliver(
                user, day, PUNCH_EVENT_TYPE, title, message, tokens, now,
                {
                    "emp_code": code,
                    "punch_time": punch_start.isoformat(),
                    "first_punch": day_first.isoformat(),
                    "elapsed_minutes": str(
                        max(0, int((punch_start - day_first).total_seconds() // 60))
                    ),
                    "elapsed_duration": elapsed,
                },
                event_key=event_key,
            )


def send_attendance_reminders(now=None):
    """Run once every minute. Punch alerts run every day; scheduled Mon-Fri."""
    now = _as_dhaka_now(now)
    day = now.date()
    employees, admin = _load_staff_and_admin()

    # Punch notifications are allowed for employees and other active users
    # with an actual machine code. Keep morning/hour reminder permissions as-is.
    punch_users = _load_punch_notification_users()
    recipients = _punch_user_by_code(punch_users)
    by_id = {u.pk: u for u in employees + punch_users}
    if admin is not None:
        by_id[admin.pk] = admin
    tokens = _token_map(list(by_id.values()))

    punches = _load_first_punches(day)
    try:
        _punch_notifications(now, recipients, tokens, day, punches)
    except Exception:
        # One machine-sync or punch-push error must not disable morning, admin,
        # or workday scheduled reminders in the same cron invocation.
        logger.exception("Attendance punch notification processing failed")

    # A punch just before midnight must still be processed when cron resumes
    # shortly after midnight, with the original attendance date/event key.
    day_start = datetime.combine(day, time.min, tzinfo=DHAKA_TZ)
    if now - day_start < PUNCH_RECENT_WINDOW + PUNCH_SETTLE_DELAY:
        prior_day = day - timedelta(days=1)
        try:
            previous_punches = _load_first_punches(prior_day)
            _punch_notifications(now, recipients, tokens, prior_day, previous_punches)
        except Exception:
            logger.exception("Previous-day attendance punch processing failed")

    # Existing morning, administrator, and workday reminders remain weekday-only.
    if now.weekday() >= 5 or not employees:
        return

    # Leave and Home Office lookups are needed only at scheduled clock times.
    is_scheduled_time = any(
        _in_window(
            now,
            datetime.combine(day, clock, tzinfo=DHAKA_TZ),
            CLOCK_WINDOW,
        )
        for clock, *_ in MORNING_REMINDERS
    ) or _in_window(
        now,
        datetime.combine(day, ADMIN_MISSING_ALERT_TIME, tzinfo=DHAKA_TZ),
        ADMIN_ALERT_WINDOW,
    )

    if is_scheduled_time:
        exempt = _exempt_from_missing_attendance(employees, day)
        _morning_reminders(now, employees, punches, exempt, tokens)
        _admin_missing_summary(now, employees, punches, exempt, tokens, admin)

    _working_hours_reminders(now, employees, punches, tokens)


def send_attendance_punch_notifications():
    """Retain the exact callable path used by settings.py CRONJOBS."""
    return send_attendance_reminders()
