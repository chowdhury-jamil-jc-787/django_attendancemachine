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

    # ✅ Create SMTP connection INSIDE function
    connection = get_connection(
        backend=settings.SIGNIN_EMAIL_BACKEND,
        host=settings.SIGNIN_EMAIL_HOST,
        port=settings.SIGNIN_EMAIL_PORT,
        username=settings.SIGNIN_EMAIL_HOST_USER,
        password=settings.SIGNIN_EMAIL_HOST_PASSWORD,
        use_ssl=settings.SIGNIN_EMAIL_USE_SSL,
        use_tls=settings.SIGNIN_EMAIL_USE_TLS,
    )

    with connections["logs"].cursor() as cursor:
        cursor.execute("""
            SELECT user_id, MIN(timestamp)
            FROM attendance_logs
            WHERE DATE(timestamp) = %s
            GROUP BY user_id
        """, [today])
        rows = cursor.fetchall()

    for emp_code, first_time in rows:
        try:
            user = User.objects.get(profile__emp_code=emp_code)
        except User.DoesNotExist:
            continue

        if DailySignInMailLog.objects.filter(user=user, date=today).exists():
            continue

        sign_in_members = MemberAssignment.objects.filter(
            user=user,
            sign_in__isnull=False
        ).select_related("sign_in")

        emails = [
            ma.sign_in.email
            for ma in sign_in_members
            if ma.sign_in and ma.sign_in.email
        ]

        if not emails:
            continue

        subject = f"({user.first_name}) Available"
        body = f"""
        <p><strong>{user.first_name}</strong> checked in at
        <strong>{first_time.strftime('%I:%M %p')}</strong> on <strong>{today}</strong>.</p>

        <p style="color:#999;font-size:12px;">
            ⚠️ This is an automated system email. Please do not reply.
        </p>
        """

        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.SIGNIN_MAIL_FROM,
            to=emails,
            connection=connection
        )
        msg.content_subtype = "html"

        # ❗ DO NOT SILENCE ERRORS
        msg.send()

        DailySignInMailLog.objects.create(user=user, date=today)
