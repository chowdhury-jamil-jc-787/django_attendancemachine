# employee/views.py
from django.db import connections
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import datetime, timedelta
from urllib.parse import urlencode
from corsheaders.defaults import default_headers


class EmployeeInfoView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        with connections['logs'].cursor() as cursor:
            cursor.execute("SELECT emp_code, first_name, last_name, email FROM personnel_employee")
            rows = cursor.fetchall()

        data = [
            {"emp_code": row[0], "first_name": row[1], "last_name": row[2], "email": row[3]}
            for row in rows
        ]
        return Response({"success": True, "data": data}, status=200)
    

class DailyFirstPunchesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_email = request.user.email
        is_admin_user = user_email.lower() == "frahman@ampec.com.au"
        params = []

        if not is_admin_user:
            with connections['logs'].cursor() as cursor:
                cursor.execute(
                    "SELECT emp_code, first_name FROM personnel_employee WHERE email = %s",
                    [user_email]
                )
                emp_data = cursor.fetchone()

            if not emp_data:
                return Response({"error": "Employee not found for this user."}, status=404)

            emp_code, first_name = emp_data

        specific_date = request.query_params.get('date')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        per_page = int(request.query_params.get('per_page', 10))
        page = int(request.query_params.get('page', 1))

        sql = """
            SELECT ic.emp_code, pe.first_name,
                   DATE(ic.punch_time) as punch_date,
                   MIN(ic.punch_time) as first_punch_time,
                   MAX(ic.punch_time) as last_punch_time
            FROM iclock_transaction ic
            JOIN personnel_employee pe ON ic.emp_code = pe.emp_code
            WHERE 1=1
        """

        if not is_admin_user:
            sql += " AND ic.emp_code = %s"
            params.append(emp_code)

        try:
            if start_date and end_date:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                sql += " AND ic.punch_time >= %s AND ic.punch_time < %s"
                params.append(start_date)
                params.append(end_date_obj.strftime("%Y-%m-%d"))
            elif start_date:
                today_plus_one = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
                sql += " AND ic.punch_time >= %s AND ic.punch_time < %s"
                params.append(start_date)
                params.append(today_plus_one)
            elif specific_date:
                sql += " AND DATE(ic.punch_time) = %s"
                params.append(specific_date)
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        sql += """
            GROUP BY ic.emp_code, pe.first_name, DATE(ic.punch_time)
            ORDER BY DATE(ic.punch_time) DESC
        """

        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS subquery"
        with connections['logs'].cursor() as cursor:
            cursor.execute(count_sql, params)
            total_count = cursor.fetchone()[0]

        offset = (page - 1) * per_page
        paginated_sql = f"""
            SELECT * FROM ({sql}) AS subquery
            LIMIT %s OFFSET %s
        """
        paginated_params = params + [per_page, offset]

        with connections['logs'].cursor() as cursor:
            cursor.execute(paginated_sql, paginated_params)
            rows = cursor.fetchall()

        # Formatting helpers
        def format_time(dt):
            return dt.strftime('%H:%M:%S') if isinstance(dt, datetime) else str(dt)

        def calculate_duration(start, end):
            if isinstance(start, datetime) and isinstance(end, datetime):
                total_minutes = int((end - start).total_seconds() // 60)
                return f"{total_minutes // 60:02}:{total_minutes % 60:02}"
            return "00:00"

        def check_arrival_status(first_punch):
            threshold = datetime.strptime('07:15:00', '%H:%M:%S').time()
            return "In Time" if isinstance(first_punch, datetime) and first_punch.time() < threshold else "Late"

        def check_leave_status(last_punch):
            threshold = datetime.strptime('15:30:00', '%H:%M:%S').time()
            return "On Time" if isinstance(last_punch, datetime) and last_punch.time() >= threshold else "Early Leave"

        results = []
        for row in rows:
            emp_code, first_name, punch_date, first_punch_dt, last_punch_dt = row
            arrival_status = check_arrival_status(first_punch_dt)
            leave_status = check_leave_status(last_punch_dt)
            combined_status = f"{arrival_status} + {leave_status}"

            results.append({
                "emp_code": emp_code,
                "first_name": first_name,
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

        # Generate all dates in the range
        all_dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        working_days = [d for d in all_dates if d.weekday() not in (5, 6)]  # Exclude Sat (5) and Sun (6)

        with connections['logs'].cursor() as cursor:
            # Get all employees
            cursor.execute("SELECT emp_code, first_name, last_name FROM personnel_employee")
            employees = cursor.fetchall()

            # Get punch data within range
            cursor.execute("""
                SELECT emp_code, DATE(punch_time), MIN(punch_time), MAX(punch_time)
                FROM iclock_transaction
                WHERE DATE(punch_time) BETWEEN %s AND %s
                GROUP BY emp_code, DATE(punch_time)
            """, [start_date, end_date])
            punch_data = cursor.fetchall()

        # Organize punch data for lookup
        from collections import defaultdict
        punch_map = defaultdict(dict)
        for emp_code, punch_date, first_punch, last_punch in punch_data:
            punch_map[emp_code][punch_date] = (first_punch, last_punch)

        results = []
        serial_no = 1

        for emp_code, first_name, last_name in employees:
            total_minutes = 0
            total_days = 0
            sum_first_punch_seconds = 0
            sum_last_punch_seconds = 0
            vacation_count = 0

            for d in working_days:
                punch = punch_map[emp_code].get(d)
                if punch:
                    first_punch, last_punch = punch
                    duration = last_punch - first_punch
                    total_minutes += duration.total_seconds() / 60
                    total_days += 1
                    sum_first_punch_seconds += first_punch.hour * 3600 + first_punch.minute * 60 + first_punch.second
                    sum_last_punch_seconds += last_punch.hour * 3600 + last_punch.minute * 60 + last_punch.second
                else:
                    vacation_count += 1

            def format_duration(minutes):
                h = int(minutes // 60)
                m = int(minutes % 60)
                return f"{h:02}:{m:02}"

            def format_avg_time(seconds):
                if total_days == 0: return "00:00:00"
                avg = int(seconds / total_days)
                h = avg // 3600
                m = (avg % 3600) // 60
                s = avg % 60
                return f"{h:02}:{m:02}:{s:02}"

            result = {
                "serial_no": serial_no,
                "employee_name": f"{first_name} {last_name}",
                "total_working_hours": format_duration(total_minutes),
                "total_working_days": total_days,
                "avg_total_working": format_duration(total_minutes / total_days if total_days else 0),
                "avg_first_punch_time": format_avg_time(sum_first_punch_seconds),
                "avg_last_punch_time": format_avg_time(sum_last_punch_seconds),
                "total_vacation": vacation_count
            }
            results.append(result)
            serial_no += 1

        return Response({
            "start_date": str(start_date),
            "end_date": str(end_date),
            "report_count": len(results),
            "report": results
        })