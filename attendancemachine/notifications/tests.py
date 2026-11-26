"""Automated tests for weekday attendance reminders.

Run with the isolated test settings (never against the live attendance DB):

    python manage.py test notifications --settings=attendancemachine.test_settings

Firebase sends and attendance-log reads are mocked. These tests neither send
real push notifications nor require the attendance machine's MySQL database.
"""

from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from attendance_monitor.models import HomeOffice
from leave.models import Leave
from profiles.models import Profile

from . import services
from .models import (
    AttendanceNotificationEvent,
    AttendancePunchNotificationLog,
    UserDeviceToken,
)


User = get_user_model()
MONDAY = date(2026, 11, 23)
SATURDAY = date(2026, 11, 21)
SUNDAY = date(2026, 11, 22)


def dhaka_time(hour, minute=0, second=0, day=MONDAY):
    return datetime.combine(
        day,
        time(hour, minute, second),
        tzinfo=services.DHAKA_TZ,
    )


def successful_push(*, tokens, title, body, data):
    """Firebase-like result with one response per device token."""
    return {
        "success": True,
        "success_count": len(tokens),
        "failure_count": 0,
        "responses": [
            SimpleNamespace(success=True, exception=None)
            for _ in tokens
        ],
    }


def failed_push(*, tokens, title, body, data):
    return {
        "success": False,
        "success_count": 0,
        "failure_count": len(tokens),
        "responses": [
            SimpleNamespace(
                success=False,
                exception=RuntimeError("Simulated Firebase outage"),
            )
            for _ in tokens
        ],
    }


class ScheduledAttendanceNotificationTests(TestCase):
    """Exercise the real scheduler, event database and eligibility rules."""

    def setUp(self):
        self.employee = self.make_user("worker_one", "101", first_name="Amina")
        self.admin = self.make_user("frahman", "999", is_staff=True)
        self.add_device(self.employee, "employee-device")
        self.add_device(self.admin, "admin-device")

        # The actual attendance log database is never queried in this class.
        punch_patcher = patch(
            "notifications.services._load_first_punches", return_value={}
        )
        self.mock_punches = punch_patcher.start()
        self.addCleanup(punch_patcher.stop)

        # No real Firebase calls are made, including in failure tests.
        firebase_patcher = patch(
            "notifications.services.send_push_to_tokens",
            side_effect=successful_push,
        )
        self.mock_firebase = firebase_patcher.start()
        self.addCleanup(firebase_patcher.stop)

    def make_user(self, username, code, **attributes):
        user = User.objects.create_user(
            username=username,
            password="TestOnlyPassword123!",
            email=f"{username}@example.com",
            **attributes,
        )
        # The project's profiles app normally creates profiles via a signal.
        # update_or_create also works if the signal is not installed in tests.
        Profile.objects.update_or_create(
            user=user,
            defaults={"emp_code": code},
        )
        # The post_save signal creates a Profile and can cache it on user.
        # update_or_create modifies a separate Profile instance, so clear
        # the stale reverse one-to-one cache before test code reads
        # user.profile.emp_code to construct mocked machine punches.
        user.refresh_from_db()
        return user

    def add_device(self, user, token, active=True):
        return UserDeviceToken.objects.create(
            user=user,
            token=token,
            platform="android",
            is_active=active,
        )

    def run_at(self, hour, minute=0, second=0, day=MONDAY):
        services.send_attendance_reminders(
            now=dhaka_time(hour, minute, second, day)
        )

    def punch_at(self, hour=8, minute=0, count=1, user=None):
        employee = user or self.employee
        # Read the current persisted employee code, not a cached Profile.
        # A wrong code in mocked punches would make the scheduler correctly
        # regard the actual employee as missing, causing false test failures.
        code = Profile.objects.get(user=employee).emp_code
        self.assertTrue(code, "A punch test requires an employee code")
        current = dict(self.mock_punches.return_value)
        current[code] = (dhaka_time(hour, minute), count)
        self.mock_punches.return_value = current

    def make_leave(self, leave_type="full_day", state="approved", approved=True):
        return Leave.objects.create(
            user=self.employee,
            leave_type=leave_type,
            reason="sick",
            date=[MONDAY.isoformat()],
            status=state,
            is_approved=approved,
        )

    def events(self, kind=None, user=None):
        queryset = AttendanceNotificationEvent.objects.all()
        if kind:
            queryset = queryset.filter(event_type=kind)
        if user:
            queryset = queryset.filter(user=user)
        return queryset

    def test_all_three_morning_reminder_times(self):
        for hour, minute in ((7, 0), (7, 45), (9, 0)):
            with self.subTest(hour=hour, minute=minute):
                self.run_at(hour, minute)
        self.assertEqual(
            set(self.events(user=self.employee).values_list("event_type", flat=True)),
            {"missing_0700", "missing_0745", "missing_0900"},
        )
        self.assertEqual(self.mock_firebase.call_count, 3)
        self.assertFalse(self.events(user=self.admin).exists())

    def test_no_morning_alert_before_start_or_after_grace_window(self):
        self.run_at(6, 59, 59)
        self.run_at(7, 5)  # The five-minute window ends at 07:05:00.
        self.run_at(7, 50)
        self.run_at(9, 5)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_morning_reminder_works_inside_grace_window(self):
        self.run_at(7, 4, 59)
        self.assertEqual(self.events(kind="missing_0700").count(), 1)
        self.assertEqual(self.mock_firebase.call_count, 1)

    def test_saturday_is_skipped_even_if_attendance_is_missing(self):
        self.run_at(7, day=SATURDAY)
        self.assertFalse(self.events().exists())
        self.mock_punches.assert_not_called()
        self.mock_firebase.assert_not_called()

    def test_sunday_is_skipped_even_at_noon(self):
        self.run_at(12, day=SUNDAY)
        self.assertFalse(self.events().exists())
        self.mock_punches.assert_not_called()
        self.mock_firebase.assert_not_called()

    def test_employee_with_a_punch_gets_no_missing_attendance_reminder(self):
        self.punch_at(6, 55)
        self.run_at(7)
        self.run_at(7, 45)
        self.run_at(9)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_missing_employee_without_device_token_is_not_recorded_as_sent(self):
        UserDeviceToken.objects.filter(user=self.employee).update(is_active=False)
        self.run_at(7)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_only_active_device_tokens_receive_notifications(self):
        self.add_device(self.employee, "inactive-device", active=False)
        self.run_at(7)
        self.assertEqual(self.mock_firebase.call_count, 1)
        self.assertEqual(
            self.mock_firebase.call_args.kwargs["tokens"],
            ["employee-device"],
        )

    def test_admin_accounts_and_reserved_code_are_not_employee_targets(self):
        other_admin = self.make_user("other_admin", "900", is_staff=True)
        super_user = self.make_user("other_superuser", "901", is_superuser=True)
        reserved = self.make_user("reserved_00", "00")
        no_code = self.make_user("no_employee_code", None)
        for user in (other_admin, super_user, reserved, no_code):
            self.add_device(user, f"token-{user.username}")
        self.run_at(7)
        recipients = set(
            self.events().values_list("user__username", flat=True)
        )
        self.assertEqual(recipients, {"worker_one"})

    def test_approved_full_day_leave_excludes_missing_attendance(self):
        self.make_leave(leave_type="full_day")
        self.run_at(7)
        self.assertFalse(self.events().exists())

    def test_approved_first_half_leave_excludes_morning_reminder(self):
        self.make_leave(leave_type="1st_half")
        self.run_at(7, 45)
        self.assertFalse(self.events().exists())

    def test_second_half_leave_does_not_exempt_missing_morning_punch(self):
        self.make_leave(leave_type="2nd_half")
        self.run_at(7)
        self.assertEqual(self.events(kind="missing_0700").count(), 1)

    def test_pending_leave_does_not_exempt_missing_attendance(self):
        self.make_leave(state="pending", approved=False)
        self.run_at(7)
        self.assertEqual(self.events(kind="missing_0700").count(), 1)

    def test_approved_cancel_pending_leave_is_still_exempt(self):
        self.make_leave(state="cancel_pending", approved=True)
        self.run_at(7)
        self.assertFalse(self.events().exists())

    def test_active_home_office_exempts_missing_attendance(self):
        HomeOffice.objects.create(
            employee=self.employee,
            date=MONDAY,
            assigned_by=self.admin,
            reason="Working remotely",
        )
        self.run_at(7)
        self.assertFalse(self.events().exists())

    def test_cancelled_home_office_no_longer_exempts_missing_attendance(self):
        HomeOffice.objects.create(
            employee=self.employee,
            date=MONDAY,
            assigned_by=self.admin,
            cancelled_at=timezone.now(),
            cancelled_by=self.admin,
        )
        self.run_at(7)
        self.assertEqual(self.events(kind="missing_0700").count(), 1)

    def test_noon_summary_goes_only_to_frahman(self):
        second_employee = self.make_user("worker_two", "102")
        self.add_device(second_employee, "second-employee-device")
        self.run_at(12)
        self.assertEqual(self.mock_firebase.call_count, 1)
        self.assertEqual(self.events(kind="noon_missing_admin", user=self.admin).count(), 1)
        self.assertEqual(self.events(user=self.employee).count(), 0)
        call = self.mock_firebase.call_args.kwargs
        self.assertEqual(call["tokens"], ["admin-device"])
        self.assertIn("2 employee(s)", call["body"])
        self.assertEqual(call["data"]["missing_count"], "2")

    def test_noon_excludes_present_and_home_office_employees(self):
        second_employee = self.make_user("worker_two", "102")
        third_employee = self.make_user("worker_three", "103")
        self.punch_at(8, user=second_employee)
        HomeOffice.objects.create(
            employee=third_employee,
            date=MONDAY,
            assigned_by=self.admin,
        )
        self.run_at(12)
        self.assertEqual(self.mock_firebase.call_count, 1)
        self.assertEqual(self.mock_firebase.call_args.kwargs["data"]["missing_count"], "1")
        self.assertIn("Amina", self.mock_firebase.call_args.kwargs["body"])

    def test_noon_sends_nothing_when_every_employee_has_punched(self):
        self.punch_at(8)
        self.run_at(12)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_noon_needs_an_active_admin_device(self):
        UserDeviceToken.objects.filter(user=self.admin).update(is_active=False)
        self.run_at(12)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_eight_nine_and_ten_hour_reminders_follow_first_punch(self):
        self.punch_at(8, count=1)
        for hour in (16, 17, 18):
            with self.subTest(hour=hour):
                self.run_at(hour)
        self.assertEqual(
            set(self.events(user=self.employee).values_list("event_type", flat=True)),
            {"workday_08h", "workday_09h", "workday_10h"},
        )
        self.assertEqual(self.mock_firebase.call_count, 3)
        self.assertEqual(
            self.mock_firebase.call_args.kwargs["data"]["elapsed_hours"],
            "10",
        )

    def test_workday_reminder_not_sent_outside_five_minute_window(self):
        self.punch_at(8)
        self.run_at(15, 59, 59)
        self.run_at(16, 5)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_multiple_distinct_punches_suppress_workday_reminder(self):
        self.punch_at(8, count=2)
        self.run_at(16)
        self.run_at(17)
        self.run_at(18)
        self.assertFalse(self.events().exists())
        self.mock_firebase.assert_not_called()

    def test_sent_event_is_not_sent_twice_by_minutely_cron(self):
        self.run_at(7)
        self.run_at(7, 1)
        self.run_at(7, 4)
        event = self.events(kind="missing_0700").get()
        self.assertEqual(event.status, "sent")
        self.assertEqual(event.attempts, 1)
        self.assertEqual(self.mock_firebase.call_count, 1)

    def test_failed_firebase_delivery_retries_after_two_minutes(self):
        self.mock_firebase.side_effect = [
            failed_push(
                tokens=["employee-device"], title="", body="", data={}
            ),
            successful_push(
                tokens=["employee-device"], title="", body="", data={}
            ),
        ]
        self.run_at(7)
        event = self.events(kind="missing_0700").get()
        self.assertEqual(event.status, "failed")
        self.assertEqual(event.attempts, 1)
        self.assertIn("Simulated Firebase outage", event.last_error)

        self.run_at(7, 1)
        event.refresh_from_db()
        self.assertEqual(event.attempts, 1)

        self.run_at(7, 2)
        event.refresh_from_db()
        self.assertEqual(event.status, "sent")
        self.assertEqual(event.attempts, 2)
        self.assertEqual(self.mock_firebase.call_count, 2)

    def test_invalid_firebase_token_is_deactivated(self):
        self.mock_firebase.side_effect = lambda **kwargs: {
            "success_count": 0,
            "failure_count": 1,
            "responses": [
                SimpleNamespace(
                    success=False,
                    exception=RuntimeError("registration-token-not-registered"),
                )
            ],
        }
        self.run_at(7)
        token = UserDeviceToken.objects.get(user=self.employee)
        token.refresh_from_db()
        self.assertFalse(token.is_active)
        self.assertEqual(self.events(kind="missing_0700").get().status, "failed")

    def test_push_data_has_string_values_for_firebase(self):
        self.run_at(7)
        payload = self.mock_firebase.call_args.kwargs["data"]
        self.assertEqual(payload["type"], "missing_0700")
        self.assertEqual(payload["date"], MONDAY.isoformat())
        self.assertTrue(all(isinstance(value, str) for value in payload.values()))

    def test_legacy_punch_notification_function_is_only_a_wrapper(self):
        with patch("notifications.services.send_attendance_reminders") as scheduler:
            services.send_attendance_punch_notifications()
        scheduler.assert_called_once_with()
        self.assertFalse(AttendancePunchNotificationLog.objects.exists())


class AttendanceLogParsingTests(SimpleTestCase):
    """Verify timestamp conversion and attendance-log SQL without MySQL."""

    def test_naive_machine_timestamp_is_assumed_dhaka_local(self):
        raw = datetime(2026, 11, 23, 8, 15)
        converted = services._dhaka_datetime(raw)
        self.assertEqual(converted.hour, 8)
        self.assertEqual(converted.utcoffset(), timedelta(hours=6))

    def test_utc_timestamp_is_converted_to_dhaka(self):
        utc_value = datetime(
            2026, 11, 23, 2, 30, tzinfo=datetime_timezone.utc
        )
        converted = services._dhaka_datetime(utc_value)
        self.assertEqual(converted.hour, 8)
        self.assertEqual(converted.minute, 30)
        self.assertEqual(converted.utcoffset(), timedelta(hours=6))

    def test_attendance_query_uses_half_open_day_range(self):
        with patch("notifications.services.connections") as mocked_connections:
            cursor = (
                mocked_connections.__getitem__.return_value
                .cursor.return_value.__enter__.return_value
            )
            cursor.fetchall.return_value = [
                ("101", datetime(2026, 11, 23, 8, 0), 1),
                ("102", datetime(2026, 11, 23, 9, 15), 2),
            ]
            punches = services._load_first_punches(MONDAY)

        sql, values = cursor.execute.call_args.args
        self.assertIn("MIN(timestamp)", sql)
        self.assertIn("COUNT(DISTINCT timestamp)", sql)
        self.assertEqual(values[0], datetime(2026, 11, 23, 0, 0))
        self.assertEqual(values[1], datetime(2026, 11, 24, 0, 0))
        self.assertEqual(punches["101"][1], 1)
        self.assertEqual(punches["102"][1], 2)
        self.assertEqual(punches["101"][0].hour, 8)
        self.assertEqual(punches["101"][0].utcoffset(), timedelta(hours=6))

    def test_invalid_attendance_timestamp_is_skipped(self):
        with patch("notifications.services.connections") as mocked_connections:
            cursor = (
                mocked_connections.__getitem__.return_value
                .cursor.return_value.__enter__.return_value
            )
            cursor.fetchall.return_value = [
                ("101", "not-a-timestamp", 1),
                (None, datetime(2026, 11, 23, 8, 0), 1),
            ]
            punches = services._load_first_punches(MONDAY)
        self.assertEqual(punches, {})
