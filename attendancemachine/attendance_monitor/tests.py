"""Automated tests for the Attendance Monitor Home Office feature.

Run against the isolated SQLite test settings:

    python manage.py test attendance_monitor --settings=attendancemachine.test_settings

The attendance machine's separate logs database is simulated in memory; these
cases do not need access to live machine logs or production MySQL tables.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from leave.models import Leave

from .models import HomeOffice


User = get_user_model()
REPORT_URL = "/api/attendance-monitor/"
HOME_OFFICE_URL = "/api/attendance-monitor/home-office/"
DAY = date(2025, 11, 25)
PREVIOUS_DAY = DAY - timedelta(days=1)


class FakeAttendanceCursor:
    """Support exactly the two read-only punch queries used by views.py."""

    def __init__(self, database):
        self.database = database
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params):
        start = date.fromisoformat(params[0])
        end = date.fromisoformat(params[1])
        punches = [
            (emp_code, punched_at)
            for emp_code, punched_at in self.database.punches
            if start <= punched_at.date() < end
        ]
        normalized = " ".join(sql.lower().split())

        if "select distinct user_id" in normalized:
            self.rows = [(code,) for code in sorted({code for code, _ in punches})]
            return

        if "count(distinct timestamp)" in normalized and "group by" in normalized:
            groups = defaultdict(list)
            for code, timestamp in punches:
                groups[(code, timestamp.date())].append(timestamp)

            self.rows = [
                (
                    code,
                    punch_day,
                    min(times),
                    max(times),
                    len(times),
                    len(set(times)),
                )
                for (code, punch_day), times in sorted(groups.items())
            ]
            return

        raise AssertionError("Unexpected attendance logs SQL in the Home Office tests")

    def fetchall(self):
        return self.rows


class FakeAttendanceLogs:
    """A writable fixture, but a read-only simulated connection for the API."""

    def __init__(self):
        self.punches = []

    def cursor(self):
        return FakeAttendanceCursor(self)

    def add_punch(self, code, day=DAY, hour=9, minute=0):
        self.punches.append((str(code), datetime(day.year, day.month, day.day, hour, minute)))


class HomeOfficeAPITests(APITestCase):
    """Exercise actual API views, serializers, permissions, and the SQLite ORM."""

    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user(
            username="frahman", password="TestPassword123!", is_staff=False
        )
        cls.manager.profile.emp_code = "00"
        cls.manager.profile.save(update_fields=["emp_code"])

        cls.staff = User.objects.create_user(
            username="staff_manager", password="TestPassword123!", is_staff=True
        )
        cls.superuser = User.objects.create_superuser(
            username="super_manager", email="super@example.test", password="TestPassword123!"
        )
        cls.normal_user = User.objects.create_user(
            username="ordinary_employee", password="TestPassword123!"
        )
        cls.employee = User.objects.create_user(
            username="employee_143",
            email="employee143@example.test",
            first_name="Test",
            last_name="Employee",
            password="TestPassword123!",
        )
        cls.employee.profile.emp_code = "143"
        cls.employee.profile.save(update_fields=["emp_code"])

    def setUp(self):
        self.logs = FakeAttendanceLogs()
        # A punch by a registered employee code confirms the office is open,
        # without making employee 143 present.
        self.logs.add_punch("00")
        self.logs_patcher = patch(
            "attendance_monitor.views.connections", {"logs": self.logs}
        )
        self.logs_patcher.start()
        self.addCleanup(self.logs_patcher.stop)

    def request_as(self, user, method, url, data=None):
        self.client.force_authenticate(user=user)
        if data is None:
            return getattr(self.client, method)(url)
        return getattr(self.client, method)(url, data, format="json")

    def assignment_payload(self, **overrides):
        return {
            "emp_code": "143",
            "date": DAY.isoformat(),
            "reason": "Approved remote work",
            **overrides,
        }

    def assign(self, user=None, **overrides):
        return self.request_as(
            self.manager if user is None else user,
            "post",
            HOME_OFFICE_URL,
            self.assignment_payload(**overrides),
        )

    def report(self, **query):
        # Deliberately use a range, not a single date. The production
        # attendance view uses MySQL JSONField `date__contains` for single-day
        # leave queries, which Django's SQLite backend doesn't support.
        params = {
            "start_date": PREVIOUS_DAY.isoformat(),
            "end_date": DAY.isoformat(),
            "emp_code": "143",
            "per_page": "100",
            **query,
        }
        from urllib.parse import urlencode
        return self.request_as(
            self.manager, "get", REPORT_URL + "?" + urlencode(params)
        )

    def employee_row(self, report):
        return next(
            row
            for row in report.data["results"]
            if row["emp_code"] == "143" and row["date"] == DAY.isoformat()
        )

    def create_record(self, cancelled=False):
        record = HomeOffice.objects.create(
            employee=self.employee,
            date=DAY,
            reason="Previously approved",
            assigned_by=self.manager,
        )
        if cancelled:
            record.cancelled_at = timezone.now()
            record.cancelled_by = self.manager
            record.cancellation_reason = "Cancelled for testing"
            record.save(update_fields=["cancelled_at", "cancelled_by", "cancellation_reason"])
        return record

    # 1-4: Permissions and ownership.

    def test_01_anonymous_cannot_list_home_offices(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(HOME_OFFICE_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_02_normal_user_cannot_list_assign_or_cancel(self):
        record = self.create_record()
        target = HOME_OFFICE_URL + f"{record.pk}/"

        self.assertEqual(
            self.request_as(self.normal_user, "get", HOME_OFFICE_URL).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.assign(user=self.normal_user).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.request_as(self.normal_user, "delete", target, {"reason": "No"}).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        record.refresh_from_db()
        self.assertIsNone(record.cancelled_at)
        self.assertEqual(HomeOffice.objects.count(), 1)

    def test_03_named_manager_can_assign_even_without_staff_flag(self):
        self.assertFalse(self.manager.is_staff)
        response = self.assign()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["home_office"]["assigned_by"]["username"], "frahman")

    def test_04_staff_and_superuser_can_assign(self):
        for user in (self.staff, self.superuser):
            with self.subTest(username=user.username):
                HomeOffice.objects.all().delete()
                response = self.assign(user=user)
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                self.assertEqual(response.data["home_office"]["assigned_by"]["id"], user.id)

    # 5-10: Validation and conflict handling.

    def test_05_assignment_rejects_unknown_employee(self):
        response = self.assign(emp_code="999999")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(HomeOffice.objects.count(), 0)

    def test_06_assignment_rejects_invalid_date(self):
        response = self.assign(date="25-11-2025")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_07_assignment_rejects_future_date(self):
        tomorrow = timezone.localdate(timezone=ZoneInfo("Asia/Dhaka")) + timedelta(days=1)
        response = self.assign(date=tomorrow.isoformat())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_08_assignment_rejects_unconfirmed_office_day(self):
        self.logs.punches.clear()
        response = self.assign()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(HomeOffice.objects.count(), 0)

    def test_09_assignment_rejects_employee_with_machine_punch(self):
        self.logs.add_punch("143", hour=9, minute=30)
        response = self.assign()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(HomeOffice.objects.count(), 0)

    def test_10_assignment_rejects_employee_with_approved_leave(self):
        Leave.objects.create(
            user=self.employee,
            leave_type="full_day",
            reason="sick",
            date=[DAY.isoformat()],
            status="approved",
            is_approved=True,
        )
        response = self.assign()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(HomeOffice.objects.count(), 0)

    def test_11_assignment_rejects_employee_with_pending_leave(self):
        Leave.objects.create(
            user=self.employee,
            leave_type="full_day",
            reason="sick",
            date=[DAY.isoformat()],
            status="pending",
        )
        response = self.assign()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_12_duplicate_active_assignment_is_rejected(self):
        first = self.assign()
        second = self.assign()
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(HomeOffice.objects.count(), 1)

    def test_13_reason_validation_rejects_non_string_and_too_long(self):
        for reason in (123, "x" * 2001):
            with self.subTest(reason_type=type(reason).__name__):
                response = self.assign(reason=reason)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(HomeOffice.objects.count(), 0)

    # 14-19: Lists, cancellation, reactivation and audit trail.

    def test_14_list_active_records_and_filter_by_date(self):
        record = self.create_record()
        response = self.request_as(
            self.manager,
            "get",
            HOME_OFFICE_URL + f"?date={DAY.isoformat()}&emp_code=143",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], record.pk)
        self.assertTrue(response.data["results"][0]["is_active"])

    def test_15_cancel_marks_record_inactive_without_deleting_it(self):
        record = self.create_record()
        response = self.request_as(
            self.manager,
            "delete",
            HOME_OFFICE_URL + f"{record.pk}/",
            {"reason": "Incorrect approval"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertFalse(record.is_active)
        self.assertEqual(record.cancelled_by_id, self.manager.id)
        self.assertEqual(record.cancellation_reason, "Incorrect approval")
        self.assertEqual(HomeOffice.objects.count(), 1)

    def test_16_cancelling_twice_returns_conflict(self):
        record = self.create_record(cancelled=True)
        response = self.request_as(
            self.manager, "delete", HOME_OFFICE_URL + f"{record.pk}/"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_17_cancelled_record_can_be_reassigned_without_duplicate(self):
        record = self.create_record(cancelled=True)
        response = self.assign(reason="Reapproved remote work")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        record.refresh_from_db()
        self.assertTrue(record.is_active)
        self.assertEqual(record.reason, "Reapproved remote work")
        self.assertIsNone(record.cancelled_by)
        self.assertEqual(HomeOffice.objects.count(), 1)

    def test_18_list_can_filter_cancelled_records(self):
        record = self.create_record(cancelled=True)
        active = self.request_as(self.manager, "get", HOME_OFFICE_URL)
        cancelled = self.request_as(
            self.manager, "get", HOME_OFFICE_URL + "?status=cancelled"
        )
        self.assertEqual(active.data["count"], 0)
        self.assertEqual(cancelled.data["count"], 1)
        self.assertEqual(cancelled.data["results"][0]["id"], record.pk)
        self.assertEqual(cancelled.data["results"][0]["status"], "cancelled")

    def test_19_nonexistent_record_cannot_be_cancelled(self):
        response = self.request_as(
            self.manager, "delete", HOME_OFFICE_URL + "999999/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # 20-23: Existing attendance reporting and original punch preservation.

    def test_20_reporting_changes_absent_to_home_office_then_back(self):
        before = self.report()
        self.assertEqual(before.status_code, status.HTTP_200_OK)
        self.assertEqual(self.employee_row(before)["status"], "absent_no_leave")
        self.assertEqual(before.data["summary"]["absent_no_leave"], 1)

        assigned = self.assign()
        self.assertEqual(assigned.status_code, status.HTTP_201_CREATED)

        during = self.report(status="home_office")
        self.assertEqual(during.status_code, status.HTTP_200_OK)
        self.assertEqual(during.data["pagination"]["total"], 1)
        self.assertEqual(self.employee_row(during)["status"], "home_office")
        self.assertEqual(during.data["summary"]["home_office"], 1)
        self.assertEqual(during.data["summary"]["absent_no_leave"], 0)
        self.assertEqual(during.data["filtered_summary"]["home_office"], 1)
        self.assertEqual(
            next(day for day in during.data["daily_summary"] if day["date"] == DAY.isoformat())["home_office"],
            1,
        )
        self.assertEqual(
            next(row for row in during.data["employee_summary"] if row["emp_code"] == "143")["home_office"],
            1,
        )

        record_id = assigned.data["home_office"]["id"]
        cancelled = self.request_as(
            self.manager, "delete", HOME_OFFICE_URL + f"{record_id}/"
        )
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        after = self.report()
        self.assertEqual(self.employee_row(after)["status"], "absent_no_leave")
        self.assertEqual(after.data["summary"]["home_office"], 0)
        self.assertEqual(after.data["summary"]["absent_no_leave"], 1)

    def test_21_original_machine_punches_unchanged_by_assignment(self):
        original = list(self.logs.punches)
        assigned = self.assign()
        self.assertEqual(assigned.status_code, status.HTTP_201_CREATED)
        record_id = assigned.data["home_office"]["id"]
        self.request_as(self.manager, "delete", HOME_OFFICE_URL + f"{record_id}/")
        self.assertEqual(self.logs.punches, original)

    def test_22_late_punch_takes_precedence_over_home_office_in_report(self):
        self.create_record()
        self.logs.add_punch("143", hour=10)
        report = self.report()
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(self.employee_row(report)["status"], "present")
        self.assertEqual(report.data["summary"]["home_office"], 0)

    def test_23_leave_takes_precedence_over_existing_home_office_in_report(self):
        self.create_record()
        Leave.objects.create(
            user=self.employee,
            leave_type="full_day",
            reason="sick",
            date=[DAY.isoformat()],
            status="approved",
            is_approved=True,
        )
        report = self.report()
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(self.employee_row(report)["status"], "approved_leave")
        self.assertEqual(report.data["summary"]["home_office"], 0)
        self.assertEqual(report.data["summary"]["leave_applications"], 1)
