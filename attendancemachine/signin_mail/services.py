from zoneinfo import ZoneInfo

from django.utils import timezone
from django.db import connections
from django.core.mail import EmailMessage, get_connection
from django.contrib.auth import get_user_model
from django.conf import settings

from member.models import MemberAssignment
from .models import DailySignInMailLog

User = get_user_model()


def send_first_signin_emails():
    today = timezone.localdate()

    # Timezones (DB time is Dhaka time)
    SYDNEY_TZ = ZoneInfo("Australia/Sydney")
    HONGKONG_TZ = ZoneInfo("Asia/Hong_Kong")
    DHAKA_TZ = ZoneInfo("Asia/Dhaka")

    # SMTP connection
    connection = get_connection(
        backend=settings.SIGNIN_EMAIL_BACKEND,
        host=settings.SIGNIN_EMAIL_HOST,
        port=settings.SIGNIN_EMAIL_PORT,
        username=settings.SIGNIN_EMAIL_HOST_USER,
        password=settings.SIGNIN_EMAIL_HOST_PASSWORD,
        use_ssl=settings.SIGNIN_EMAIL_USE_SSL,
        use_tls=settings.SIGNIN_EMAIL_USE_TLS,
    )

    # Get first check-in per user (logs DB)
    with connections["logs"].cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, MIN(timestamp)
            FROM attendance_logs
            WHERE DATE(timestamp) = %s
            GROUP BY user_id
            """,
            [today],
        )
        rows = cursor.fetchall()

    for emp_code, first_time in rows:
        # emp_code here is coming from attendance_logs.user_id (as per your original code)
        try:
            user = User.objects.get(profile__emp_code=emp_code)
        except User.DoesNotExist:
            continue

        # Avoid duplicate mail per day
        if DailySignInMailLog.objects.filter(user=user, date=today).exists():
            continue

        # Find recipients
        sign_in_members = (
            MemberAssignment.objects.filter(user=user, sign_in__isnull=False)
            .select_related("sign_in")
        )

        emails = [
            ma.sign_in.email
            for ma in sign_in_members
            if ma.sign_in and ma.sign_in.email
        ]
        if not emails:
            continue

        # ✅ DB time is already Dhaka time
        # If it's naive, just attach Dhaka tzinfo (does NOT change 08:03 -> something else)
        if timezone.is_naive(first_time):
            first_time = first_time.replace(tzinfo=DHAKA_TZ)

        # Convert only for display
        sydney_time = first_time.astimezone(SYDNEY_TZ)
        hongkong_time = first_time.astimezone(HONGKONG_TZ)

        subject = f"({user.first_name}) Available"
        body = f"""
        <p><strong>{user.first_name}</strong> checked in on <strong>{today}</strong>.</p>

        <ul>
            <li><strong>Sydney Time:</strong> {sydney_time.strftime('%I:%M %p')}</li>
            <li><strong>Dhaka Time:</strong> {first_time.strftime('%I:%M %p')}</li>
            <li><strong>Hong Kong Time:</strong> {hongkong_time.strftime('%I:%M %p')}</li>
        </ul>

        <p style="color:#999;font-size:12px;">
            ⚠️ This is an automated system email. Please do not reply.
        </p>
        """

        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.SIGNIN_MAIL_FROM,
            to=emails,
            connection=connection,
        )
        msg.content_subtype = "html"

        # Send (do not silence errors)
        msg.send()

        # Log sent mail
        DailySignInMailLog.objects.create(user=user, date=today)
