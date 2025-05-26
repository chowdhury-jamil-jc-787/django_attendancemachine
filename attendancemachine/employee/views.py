# employee/views.py
from django.db import connections
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import datetime, timedelta
from urllib.parse import urlencode
from corsheaders.defaults import default_headers
from collections import defaultdict
from django.contrib.auth.models import User

class EmployeeInfoView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        users = User.objects.select_related('profile').all()
        width = request.GET.get('w', 150)
        height = request.GET.get('h', 150)

        data = []
        for user in users:
            profile = user.profile
            emp_code = getattr(profile, 'emp_code', None)

            # Skip employee with emp_code "00"
            if emp_code == "00":
                continue

            # Generate resized image URL if profile image exists
            if profile.profile_img:
                original_path = profile.profile_img.url  # e.g., /media/profile_images/imran.jpg
                resized_url = f"/api/profiles/resize/?path={original_path}&w={width}&h={height}"
            else:
                resized_url = None

            data.append({
                'emp_code': emp_code,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'profile_img': resized_url
            })

        return Response({"success": True, "data": data}, status=200)


class DailyFirstPunchesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        emp_code = request.query_params.get('emp_code')
        user_email = user.email.lower()
        is_admin_user = user_email == "frahman@ampec.com.au"

        # For regular user, get emp_code from profile
        if not is_admin_user:
            emp_code = getattr(user.profile, 'emp_code', None)
            if not emp_code:
                return Response({"error": "Employee code not found for this user."}, status=404)

        specific_date = request.query_params.get('date')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        per_page = int(request.query_params.get('per_page', 10))
        page = int(request.query_params.get('page', 1))

        params = []
        sql = """
            SELECT user_id,
                   DATE(timestamp) as punch_date,
                   MIN(timestamp) as first_punch_time,
                   MAX(timestamp) as last_punch_time
            FROM attendance_logs
            WHERE 1=1
        """

        if emp_code:
            sql += " AND user_id = %s"
            params.append(emp_code)

        try:
            if start_date and end_date:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                sql += " AND timestamp >= %s AND timestamp < %s"
                params.append(start_date)
                params.append(end_date_obj.strftime("%Y-%m-%d"))
            elif start_date:
                today_plus_one = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                sql += " AND timestamp >= %s AND timestamp < %s"
                params.append(start_date)
                params.append(today_plus_one)
            elif specific_date:
                sql += " AND DATE(timestamp) = %s"
                params.append(specific_date)
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        sql += """
            GROUP BY user_id, DATE(timestamp)
            ORDER BY DATE(timestamp) DESC
        """

        # Count total results
        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS subquery"
        with connections['logs'].cursor() as cursor:
            cursor.execute(count_sql, params)
            total_count = cursor.fetchone()[0]

        # Pagination
        offset = (page - 1) * per_page
        paginated_sql = f"""
            SELECT * FROM ({sql}) AS subquery
            LIMIT %s OFFSET %s
        """
        paginated_params = params + [per_page, offset]

        with connections['logs'].cursor() as cursor:
            cursor.execute(paginated_sql, paginated_params)
            rows = cursor.fetchall()

        # Build emp_code → (first_name, last_name) map
        user_map = {}
        for u in User.objects.select_related('profile').all():
            emp_code_key = getattr(u.profile, 'emp_code', None)
            if emp_code_key:
                user_map[emp_code_key] = (u.first_name, u.last_name)

        # Utilities
        def format_time(dt):
            return dt.strftime('%H:%M:%S') if isinstance(dt, datetime) else str(dt)

        def calculate_duration(start, end):
            if isinstance(start, datetime) and isinstance(end, datetime):
                total_minutes = int((end - start).total_seconds() // 60)
                return f"{total_minutes // 60:02}:{total_minutes % 60:02}"
            return "00:00"

        def check_arrival_status(first_punch):
            if isinstance(first_punch, datetime):
                threshold_time = datetime.strptime('07:15:59', '%H:%M:%S').time()
                if first_punch.time() > threshold_time:
                    base_time = datetime.combine(first_punch.date(), datetime.strptime('07:00:00', '%H:%M:%S').time())
                    delta = first_punch - base_time
                    minutes_late = int(delta.total_seconds() // 60)
                    return f"Late ({minutes_late} mins)"
            return ""

        def check_leave_status(first_punch, last_punch):
            if isinstance(first_punch, datetime) and isinstance(last_punch, datetime):
                actual_duration = last_punch - first_punch
                threshold = timedelta(hours=8, minutes=30)
                full_required = timedelta(hours=9)

                if actual_duration < threshold:
                    minutes_short = int((full_required - actual_duration).total_seconds() // 60)
                    return f"Early Leave ({minutes_short} mins)"
            return ""

        # Build response
        results = []
        for row in rows:
            emp_code_row, punch_date, first_punch_dt, last_punch_dt = row
            arrival_status = check_arrival_status(first_punch_dt)
            leave_status = check_leave_status(first_punch_dt, last_punch_dt)
            combined_status = " + ".join(filter(None, [arrival_status, leave_status]))
            first_name, last_name = user_map.get(emp_code_row, ("", ""))

            results.append({
                "emp_code": emp_code_row,
                "first_name": first_name,
                "last_name": last_name,
                "date": str(punch_date),
                "first_punch_time": format_time(first_punch_dt),
                "last_punch_time": format_time(last_punch_dt),
                "total_hour": calculate_duration(first_punch_dt, last_punch_dt),
                "status": combined_status
            })

        # Pagination metadata
        last_page = (total_count + per_page - 1) // per_page
        base_url = request.build_absolute_uri(request.path)
        base_params = request.query_params.dict()
        base_params['per_page'] = per_page

        def build_url(page_number):
            base_params['page'] = page_number
            return f"{base_url}?{urlencode(base_params)}"

        pagination = {
            "total": total_count,
            "per_page": per_page,
            "current_page": page,
            "last_page": last_page,
            "next_page_url": build_url(page + 1) if page < last_page else None,
            "prev_page_url": build_url(page - 1) if page > 1 else None
        }

        return Response({
            "pagination": pagination,
            "results": results
        })

class AttendanceSummaryReport(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            start_date = datetime.strptime(request.data.get('start_date'), "%Y-%m-%d").date()
            end_date = datetime.strptime(request.data.get('end_date'), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return Response({"error": "Invalid or missing date format. Use YYYY-MM-DD."}, status=400)

        if end_date < start_date:
            return Response({"error": "end_date cannot be before start_date."}, status=400)

        all_dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        working_days = [d for d in all_dates if d.weekday() not in (5, 6)]

        users = User.objects.select_related('profile').all()
        employee_map = {
            u.profile.emp_code: {
                'name': f"{u.first_name} {u.last_name}"
            }
            for u in users
            if hasattr(u, 'profile') and u.profile.emp_code
        }

        with connections['logs'].cursor() as cursor:
            cursor.execute("""
                SELECT user_id, DATE(timestamp), MIN(timestamp), MAX(timestamp)
                FROM attendance_logs
                WHERE DATE(timestamp) BETWEEN %s AND %s
                GROUP BY user_id, DATE(timestamp)
            """, [start_date, end_date])
            punch_data = cursor.fetchall()

        punch_map = defaultdict(dict)
        for user_id, punch_date, first_punch, last_punch in punch_data:
            punch_map[user_id][punch_date] = (first_punch, last_punch)

        results = []
        serial_no = 1

        for emp_code, info in employee_map.items():
            if emp_code == "00":
                continue  # Skip this employee

            total_minutes = 0
            total_days = 0
            sum_first_punch_seconds = 0
            sum_last_punch_seconds = 0
            vacation_count = 0

            less_8_30 = 0
            between_8_30_and_9_00 = 0
            greater_9_00 = 0

            before_7am = 0
            between_7_and_7_15_59 = 0
            after_7_15_59 = 0

            for d in working_days:
                punch = punch_map[emp_code].get(d)
                if punch:
                    first_punch, last_punch = punch
                    duration = last_punch - first_punch
                    duration_minutes = duration.total_seconds() / 60
                    duration_hours = duration.total_seconds() / 3600

                    total_minutes += duration_minutes
                    total_days += 1
                    sum_first_punch_seconds += first_punch.hour * 3600 + first_punch.minute * 60 + first_punch.second
                    sum_last_punch_seconds += last_punch.hour * 3600 + last_punch.minute * 60 + last_punch.second

                    if duration_hours < 8.5:
                        less_8_30 += 1
                    elif 8.5 <= duration_hours <= 9:
                        between_8_30_and_9_00 += 1
                    else:
                        greater_9_00 += 1

                    punch_time = first_punch.time()
                    if punch_time < datetime.strptime("07:00:00", "%H:%M:%S").time():
                        before_7am += 1
                    elif punch_time <= datetime.strptime("07:15:59", "%H:%M:%S").time():
                        between_7_and_7_15_59 += 1
                    else:
                        after_7_15_59 += 1
                else:
                    vacation_count += 1

            def format_duration(minutes):
                h = int(minutes // 60)
                m = int(minutes % 60)
                return f"{h:02}:{m:02}"

            def format_avg_time(seconds):
                if total_days == 0:
                    return "00:00:00"
                avg = int(seconds / total_days)
                h = avg // 3600
                m = (avg % 3600) // 60
                s = avg % 60
                return f"{h:02}:{m:02}:{s:02}"

            result = {
                "serial_no": serial_no,
                "emp_code": emp_code,
                "employee_name": info['name'],
                "total_working_hours": format_duration(total_minutes),
                "total_working_days": total_days,
                "avg_hours_per_day": format_duration(total_minutes / total_days if total_days else 0),
                "avg_sign_in": format_avg_time(sum_first_punch_seconds),
                "avg_sign_out": format_avg_time(sum_last_punch_seconds),
                "total_vacation": vacation_count,
                "less_8_30": less_8_30,
                "between_8_30_and_9_00": between_8_30_and_9_00,
                "greater_9_00": greater_9_00,
                "before_7am": before_7am,
                "between_7_and_7_15_59": between_7_and_7_15_59,
                "after_7_15_59": after_7_15_59
            }

            results.append(result)
            serial_no += 1

        return Response({
            "start_date": str(start_date),
            "end_date": str(end_date),
            "report_count": len(results),
            "report": results
        })












