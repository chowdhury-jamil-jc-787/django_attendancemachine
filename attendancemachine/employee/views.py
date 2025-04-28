# employee/views.py
from django.db import connections
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from datetime import datetime, timedelta
from urllib.parse import urlencode

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
        user_email = request.user.email  # Authenticated user's email

        # Step 1: Get emp_code for the authenticated user
        with connections['logs'].cursor() as cursor:
            cursor.execute(
                "SELECT emp_code, first_name FROM personnel_employee WHERE email = %s",
                [user_email]
            )
            emp_data = cursor.fetchone()

        if not emp_data:
            return Response({"error": "Employee not found for this user."}, status=404)

        emp_code, first_name = emp_data

        # Step 2: Handle filters
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
            WHERE ic.emp_code = %s
        """
        params = [emp_code]

        if specific_date:
            sql += " AND DATE(ic.punch_time) = %s"
            params.append(specific_date)
        if start_date and end_date:
            try:
                end_date_obj = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                sql += " AND ic.punch_time >= %s AND ic.punch_time < %s"
                params.append(start_date)
                params.append(end_date_obj.strftime("%Y-%m-%d"))
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=400)

        sql += """
            GROUP BY ic.emp_code, pe.first_name, DATE(ic.punch_time)
            ORDER BY DATE(ic.punch_time) DESC
        """

        # Step 3: Pagination
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

        def format_time(dt):
            if isinstance(dt, datetime):
                return dt.strftime('%H:%M:%S')
            elif isinstance(dt, str) and len(dt) >= 19:
                return dt[11:19]
            return str(dt)

        def calculate_duration(start, end):
            if isinstance(start, datetime) and isinstance(end, datetime):
                diff = end - start
                total_minutes = int(diff.total_seconds() // 60)
                hours = total_minutes // 60
                minutes = total_minutes % 60
                return f"{hours:02}:{minutes:02}"
            return "00:00"

        results = []
        for row in rows:
            emp_code, first_name, punch_date, first_punch_dt, last_punch_dt = row
            results.append({
                "emp_code": emp_code,
                "first_name": first_name,
                "date": str(punch_date),
                "first_punch_time": format_time(first_punch_dt),
                "last_punch_time": format_time(last_punch_dt),
                "total_hour": calculate_duration(first_punch_dt, last_punch_dt)
            })

        base_params = request.query_params.dict()
        base_params['per_page'] = per_page

        def build_url(page_number):
            base_params['page'] = page_number
            return f"?{urlencode(base_params)}"

        response = {
            "count": total_count,
            "next": build_url(page + 1) if offset + per_page < total_count else None,
            "previous": build_url(page - 1) if page > 1 else None,
            "results": results,
        }

        return Response(response)
    

    