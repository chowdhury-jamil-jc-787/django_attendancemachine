# notifications/services.py

from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connections, IntegrityError
from django.utils import timezone

from .models import UserDeviceToken, AttendancePunchNotificationLog
from .firebase import send_push_to_tokens

User = get_user_model()


def send_attendance_punch_notifications():
    DHAKA_TZ = ZoneInfo("Asia/Dhaka")
    today = timezone.localdate()

    with connections["logs"].cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, timestamp
            FROM attendance_logs
            WHERE DATE(timestamp) = %s
            ORDER BY timestamp ASC
            """,
            [today]
        )
        rows = cursor.fetchall()

    for emp_code, punch_time in rows:
        emp_code = str(emp_code)

        try:
            user = User.objects.select_related("profile").get(profile__emp_code=emp_code)
        except User.DoesNotExist:
            continue

        if timezone.is_naive(punch_time):
            punch_time = punch_time.replace(tzinfo=DHAKA_TZ)

        try:
            AttendancePunchNotificationLog.objects.create(
                user=user,
                emp_code=emp_code,
                punch_time=punch_time
            )
        except IntegrityError:
            continue

        tokens = list(
            UserDeviceToken.objects.filter(
                user=user,
                is_active=True
            ).values_list("token", flat=True)
        )

        if not tokens:
            continue

        display_time = punch_time.astimezone(DHAKA_TZ).strftime("%d %b %Y, %I:%M %p")
        first_name = user.first_name or user.username

        title = "Attendance Punch"
        body = f"{first_name}, you punched at {display_time}."

        result = send_push_to_tokens(
            tokens=tokens,
            title=title,
            body=body,
            data={
                "type": "attendance_punch",
                "user_id": str(user.id),
                "emp_code": emp_code,
                "punch_time": punch_time.isoformat(),
                "date": today.isoformat(),
            }
        )

        responses = result.get("responses", [])
        for index, response in enumerate(responses):
            if not response.success:
                error_text = str(response.exception)

                if (
                    "registration-token-not-registered" in error_text
                    or "invalid-registration-token" in error_text
                ):
                    UserDeviceToken.objects.filter(token=tokens[index]).update(is_active=False)