"""Existing notification endpoints with an opt-in, read-only cron diagnostic."""

import logging
import os
import secrets
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import DatabaseError, connections
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import AttendanceNotificationEvent, UserDeviceToken
from .serializers import UserDeviceTokenSerializer
from .services import send_attendance_reminders

logger = logging.getLogger(__name__)


class SaveDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UserDeviceTokenSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Device token saved successfully.",
                "device": serializer.data,
            }, status=201)
        return Response({
            "success": False,
            "message": "Could not save device token.",
            "errors": serializer.errors,
        }, status=400)


class DeleteDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("token")
        if not token:
            return Response({
                "success": False, "message": "token is required."
            }, status=400)
        UserDeviceToken.objects.filter(user=request.user, token=token).update(
            is_active=False
        )
        return Response({
            "success": True, "message": "Device token removed successfully."
        })


def _safe_db_error(exc):
    """Expose bounded DB diagnostics only to the secret-authorized request."""
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    code = None
    sqlstate = None
    for item in chain:
        code = code or getattr(item, "errno", None)
        sqlstate = sqlstate or getattr(item, "sqlstate", None)
        if code is None and item.args and isinstance(item.args[0], int):
            code = item.args[0]
    message = str(chain[-1])
    # No SQL parameters, token values, configuration paths or traceback are returned.
    # Redact configured database passwords and the cron secret if present in an error.
    sensitive = [getattr(settings, "ATTENDANCE_CRON_SECRET", "")]
    sensitive.extend(db.get("PASSWORD", "") for db in settings.DATABASES.values())
    for value in sensitive:
        if isinstance(value, str) and value:
            message = message.replace(value, "[redacted]")
    return {"error_type": type(exc).__name__, "driver_error_type": type(chain[-1]).__name__,
            "database_errno": code, "sqlstate": sqlstate,
            "database_message": message[:350]}


def _diagnose_punches(username):
    """Inspect production data using v4 helpers; never send or claim events."""
    stage = "server_time"
    progress = {}
    user = None
    day = None
    first = None
    try:
        now = services._as_dhaka_now(None)
        day = now.date()
        progress["server_time"] = now.isoformat()
        stage = "recipient_lookup"
        users = services._load_punch_notification_users()
        user = next((u for u in users if u.username == username), None)
        if user is None:
            return Response({
                "success": False,
                "message": "No active user with that username and a machine code.",
            }, status=status.HTTP_404_NOT_FOUND)
        code = services._employee_code(user)
        recipient = services._punch_user_by_code(users).get(code)
        recipient_ok = recipient is not None and recipient.pk == user.pk

        stage = "firebase_configuration"
        credential = getattr(settings, "FIREBASE_SERVICE_ACCOUNT_FILE", None)
        credential_exists = bool(credential and os.path.isfile(credential))
        credential_readable = bool(credential_exists and os.access(credential, os.R_OK))
        tokens = UserDeviceToken.objects.filter(user_id=user.pk, is_active=True).count()

        progress.update({"recipient_eligible": recipient_ok, "active_tokens": tokens,
                         "firebase_credential_readable": credential_readable})
        stage = "first_punch_query"
        first_punches = services._load_first_punches(day)
        summary = first_punches.get(code)
        stage = "latest_machine_punch_query"
        day_start = datetime.combine(day, time.min)
        day_end = day_start + timedelta(days=1)
        with connections["logs"].cursor() as cursor:
            cursor.execute(
                "SELECT MAX(timestamp) FROM attendance_logs "
                "WHERE user_id = %s AND timestamp >= %s AND timestamp < %s",
                [code, services._machine_sql_timestamp(day_start),
                 services._machine_sql_timestamp(day_end)],
            )
            row = cursor.fetchone()
        latest = services._dhaka_datetime(row[0]) if row and row[0] is not None else None

        progress.update({"first_punch": summary[0].isoformat() if summary else None,
                         "latest_machine_punch": latest.isoformat() if latest else None})
        stage = "recent_punch_query"
        recent = services._load_recent_punches(day, now).get(code, [])
        stage = "group_recent_punches"
        groups = []
        clusters = services._group_punches(recent)
        progress["recent_group_count"] = len(clusters)
        for first, last in clusters:
            progress["current_punch"] = first.isoformat()
            age = now - last
            in_window = services.PUNCH_SETTLE_DELAY <= age <= services.PUNCH_RECENT_WINDOW
            stage = "stable_event_key_lookup"
            key = services._stable_punch_key(user, day, first)
            stage = "exact_event_lookup"
            event = AttendanceNotificationEvent.objects.filter(event_key=key).values(
                "status", "attempts", "last_attempt_at", "sent_at"
            ).first()
            if not recipient_ok:
                reason = "invalid_or_ambiguous_employee_code"
            elif not tokens:
                reason = "no_active_token"
            elif not summary:
                reason = "no_first_punch"
            elif age < services.PUNCH_SETTLE_DELAY:
                reason = "waiting_for_duplicate_settle_delay"
            elif age > services.PUNCH_RECENT_WINDOW:
                reason = "outside_recent_window"
            elif event and event["status"] == "sent":
                reason = "already_sent"
            elif event and event["status"] == "sending" and event["last_attempt_at"] and now - event["last_attempt_at"] < services.STALE_CLAIM_DELAY:
                reason = "active_delivery_claim"
            elif event and event["status"] == "failed" and event["last_attempt_at"] and now - event["last_attempt_at"] < services.RETRY_DELAY:
                reason = "retry_delay"
            else:
                reason = "eligible_for_processing"
            groups.append({
                "first": first.isoformat(), "last": last.isoformat(),
                "age_seconds": int(age.total_seconds()),
                "in_processing_window": in_window,
                "event_key": key,
                "decision": reason,
                "existing_event": event,
            })

        stage = "notification_history"
        history = list(AttendanceNotificationEvent.objects.filter(
            user_id=user.pk, attendance_date=day
        ).order_by("-id").values(
            "event_type", "event_key", "status", "attempts", "sent_at"
        )[:10])
        return Response({
            "success": True,
            "diagnostic_version": "v4-read-only-2",
            "server_time": now.isoformat(),
            "username": user.username,
            "recipient_eligible": recipient_ok,
            "active_tokens": tokens,
            "firebase_credential_exists": credential_exists,
            "firebase_credential_readable": credential_readable,
            "first_punch": summary[0].isoformat() if summary else None,
            "distinct_punch_count": summary[1] if summary else 0,
            "latest_machine_punch": latest.isoformat() if latest else None,
            "recent_groups": groups,
            "notification_history": history,
            "message": "Read-only diagnostic; no notifications sent.",
        })
    except Exception as exc:
        logger.exception("Attendance cron diagnostic failed at %s", stage)
        details = {"error_type": type(exc).__name__}
        if isinstance(exc, DatabaseError) and stage in (
            "stable_event_key_lookup", "exact_event_lookup", "notification_history"
        ):
            details.update(_safe_db_error(exc))
        probes = {}
        if stage == "stable_event_key_lookup" and user is not None and day is not None:
            # Compare the failing startswith lookup with a plain filtered read.
            # These queries are read-only and never return actual keys or tokens.
            try:
                keys = list(AttendanceNotificationEvent.objects.filter(
                    user_id=user.pk, attendance_date=day,
                    event_type=services.PUNCH_EVENT_TYPE,
                ).values_list("event_key", flat=True))
                probes["without_startswith"] = {"success": True, "count": len(keys)}
            except DatabaseError as probe_exc:
                probes["without_startswith"] = {"success": False, **_safe_db_error(probe_exc)}
            if first is not None:
                try:
                    candidate = f"{day.isoformat()}:{user.pk}:{services.PUNCH_EVENT_TYPE}:{first.strftime('%H%M%S')}"
                    exists = AttendanceNotificationEvent.objects.filter(event_key=candidate).exists()
                    probes["exact_key"] = {"success": True, "exists": exists}
                except DatabaseError as probe_exc:
                    probes["exact_key"] = {"success": False, **_safe_db_error(probe_exc)}
        return Response({
            "success": False,
            "diagnostic_version": "v4-read-only-2",
            "stage": stage,
            **details,
            "checks_completed": progress,
            "comparison_queries": probes,
            "message": "Read-only diagnostic failed; no notifications sent.",
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


@method_decorator(never_cache, name="dispatch")
class ProcessScheduledAttendanceNotificationsView(APIView):
    """Existing secret-protected cron endpoint; optional POST diagnostic."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return self._process(request)

    def post(self, request, *args, **kwargs):
        return self._process(request)

    def _process(self, request):
        expected_secret = getattr(settings, "ATTENDANCE_CRON_SECRET", "")
        if not isinstance(expected_secret, str) or not expected_secret:
            logger.error("Attendance cron secret is not configured.")
            return Response({
                "success": False, "message": "Cron service is not configured."
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        supplied_secret = (
            request.headers.get("X-Attendance-Cron-Secret")
            or request.query_params.get("secret", "")
        )
        if not isinstance(supplied_secret, str) or not secrets.compare_digest(
            supplied_secret.encode("utf-8"), expected_secret.encode("utf-8")
        ):
            return Response({
                "success": False, "message": "Forbidden."
            }, status=status.HTTP_403_FORBIDDEN)

        # Diagnostics require POST and the secret header (not a URL query secret).
        diagnostic_user = request.headers.get("X-Attendance-Diagnostic-User")
        if diagnostic_user is not None:
            if request.method != "POST" or not request.headers.get("X-Attendance-Cron-Secret"):
                return Response({
                    "success": False, "message": "POST and cron secret header required."
                }, status=status.HTTP_403_FORBIDDEN)
            diagnostic_user = diagnostic_user.strip()
            if not diagnostic_user or len(diagnostic_user) > 150:
                return Response({
                    "success": False, "message": "Invalid diagnostic username."
                }, status=status.HTTP_400_BAD_REQUEST)
            return _diagnose_punches(diagnostic_user)

        # Normal cron execution and response remain unchanged.
        try:
            send_attendance_reminders()
        except Exception:
            logger.exception("Attendance cron processing failed.")
            return Response({
                "success": False, "message": "Attendance processing failed."
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({
            "success": True,
            "message": "Scheduled attendance notification processing completed.",
        }, status=status.HTTP_200_OK)
