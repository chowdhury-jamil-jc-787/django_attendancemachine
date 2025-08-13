from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404, redirect
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.http import Http404, HttpResponse
from django.utils.html import escape

from .serializers import LeaveSerializer
from .models import Leave
from .utils import send_leave_email, correct_grammar_and_paraphrase

from types import SimpleNamespace
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework import generics
from django.db.models import Q
from django.utils.dateparse import parse_datetime, parse_date
from rest_framework.pagination import PageNumberPagination
from .serializers import LeaveListSerializer
from django.contrib.sites.shortcuts import get_current_site
from .serializers import LeaveSerializer
from rest_framework import generics, serializers, status
from rest_framework.utils.urls import replace_query_param




class LeaveRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        print("📥 Received leave request:", request.data)

        serializer = LeaveSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            leave = serializer.save(user=request.user)
            print("✅ Leave saved with ID:", leave.id)

            corrected_reason = (
                correct_grammar_and_paraphrase(leave.reason or "")
                if leave.leave_type in ('1st_half', '2nd_half') else leave.reason
            )

            domain = get_current_site(request).domain
            approve_url = f"http://{domain}{reverse('leave-approval', args=[leave.id])}?action=approve"
            reject_url = f"http://{domain}{reverse('leave-approval', args=[leave.id])}?action=reject"

            print("🧠 Final reason used:", corrected_reason)

            email_body = send_leave_email(
                request.user,
                leave,
                corrected_reason,
                approve_url=approve_url,
                reject_url=reject_url
            )
            leave.email_body = email_body
            leave.save()
            print("✉️ Email sent and body saved.")

            return Response({
                "message": "Leave request submitted successfully",
                "data": LeaveSerializer(leave, context={'request': request}).data
            }, status=status.HTTP_201_CREATED)

        print("❌ Serializer errors:", serializer.errors)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LeaveApprovalView(APIView):
    permission_classes = [AllowAny]  # ✅ Still allow public access from email

    def get(self, request, pk):
        """
        Redirect to frontend leave review page (no approval/rejection here).
        """
        action = request.GET.get("action")
        leave = get_object_or_404(Leave, pk=pk)

        # Redirect to frontend review page
        frontend_url = f"https://atpldhaka.com/leave-review/{leave.id}?action={action}"
        return redirect(frontend_url)


class LeaveDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        # Only 'frahman' can take action
        if request.user.username != "frahman":
            return Response({"error": "You are not authorized to perform this action."}, status=403)

        action = request.data.get("action")
        if action not in ["approve", "reject"]:
            return Response({"error": "Invalid action."}, status=400)

        # Fetch only existing fields (avoid old start_date/end_date)
        row = (Leave.objects
               .filter(pk=pk)
               .values("id", "leave_type", "reason", "status", "is_approved",
                       "user_id", "email_body", "informed_status", "date")
               .first())
        if not row:
            raise Http404("Leave not found.")

        if row["status"] != "pending":
            return Response({"message": f"Leave is already {row['status']}."}, status=400)

        new_status = "approved" if action == "approve" else "rejected"
        new_is_approved = (action == "approve")

        # Atomic update without loading full model instance
        with transaction.atomic():
            updated = (Leave.objects
                .filter(pk=pk, status="pending")
                .update(status=new_status, is_approved=new_is_approved, updated_at=timezone.now()))
            if updated == 0:
                return Response({"message": "Leave status already changed."}, status=400)

        # Load user (only needed fields)
        User = get_user_model()
        user = User.objects.only("id", "email", "username", "first_name", "last_name").get(pk=row["user_id"])

        # Normalize date to a list of strings for the template
        raw_date = row.get("date")
        if isinstance(raw_date, str):
            date_list = [raw_date]
        elif isinstance(raw_date, list):
            date_list = raw_date
        else:
            date_list = []

        # Grammar correction for half-day types only
        corrected_reason = (
            correct_grammar_and_paraphrase(row["reason"] or "")
            if row["leave_type"] in ("1st_half", "2nd_half")
            else row["reason"]
        )

        # Provide a display label like get_leave_type_display
        display_map = {"full_day": "Full Day", "1st_half": "First Half", "2nd_half": "Second Half"}

        # Lightweight object for templates
        leave_ns = SimpleNamespace(
            id=row["id"],
            leave_type=row["leave_type"],
            reason=row["reason"],
            status=new_status,
            is_approved=new_is_approved,
            email_body=row.get("email_body"),
            informed_status=row.get("informed_status"),
            date=date_list,  # always a list now
            get_leave_type_display=display_map.get(row["leave_type"], row["leave_type"]),
        )

        # Notify requester
        try:
            context = {"user": user, "leave": leave_ns, "corrected_reason": corrected_reason}
            subject = f"Your Leave Request Has Been {new_status.upper()}"
            body = render_to_string("leave/leave_decision_email.html", context)
            email = EmailMessage(subject, body, to=[user.email])
            email.content_subtype = "html"
            email.send()
        except Exception as e:
            print(f"❌ Failed to notify user: {str(e)}")

        # Notify admin (optional)
        try:
            display_name = (user.get_full_name() or user.username)
            admin_email = EmailMessage(
                subject=f"You have {action}ed a leave request",
                body=f"You have {action}ed {display_name}'s leave request.",
                to=["jamil@ampec.com.au"]
            )
            admin_email.send()
        except Exception as e:
            print(f"❌ Failed to notify admin: {str(e)}")

        return Response({"message": f"Leave has been {new_status}."}, status=200)





# ---------------------------
# helpers kept inside views.py
# ---------------------------

def resolve_user_ids_from_emp_code(emp_code: str):
    """
    Return a list of user IDs that match the given employee code.
    - Tries Employee and Profile common fields.
    - Falls back to matching User by username/email.
    - ALSO accepts a numeric user ID string, e.g., "72".
    """
    user_ids = set()

    # Try employee app
    try:
        from employee.models import Employee  # type: ignore
        q = Q()
        for fld in ['emp_code', 'employee_code', 'code', 'employee_id', 'employee_no']:
            q |= Q(**{fld: emp_code})
        user_ids.update(Employee.objects.filter(q).values_list('user_id', flat=True))
    except Exception:
        pass

    # Try profiles app
    try:
        from profiles.models import Profile  # type: ignore
        q = Q()
        for fld in ['employee_code', 'emp_code', 'code', 'employee_id', 'employee_no']:
            q |= Q(**{fld: emp_code})
        user_ids.update(Profile.objects.filter(q).values_list('user_id', flat=True))
    except Exception:
        pass

    # Fallback: match username/email
    try:
        User = get_user_model()
        user_ids.update(
            User.objects.filter(Q(username=emp_code) | Q(email=emp_code)).values_list('id', flat=True)
        )
    except Exception:
        pass

    # NEW: allow numeric User ID
    try:
        uid = int(str(emp_code).strip())
        User = get_user_model()
        if User.objects.filter(id=uid).exists():
            user_ids.add(uid)
    except Exception:
        pass

    return list(user_ids)


def parse_bool(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return None
    return str(val).strip().lower() in ("1", "true", "t", "yes", "y")

def parse_int(val):
    try:
        return int(str(val).strip())
    except Exception:
        return None

ALLOWED_ORDER_FIELDS = {
    'id', 'leave_type', 'reason', 'status', 'is_approved',
    'informed_status', 'created_at', 'updated_at'
}


class AmpecPagination(PageNumberPagination):
    page_size = 15                      # default per_page
    page_size_query_param = 'per_page'  # ?per_page=...
    max_page_size = 200
    page_query_param = 'page'           # ?page=...

    def _build_url(self, page_number):
        request = self.request
        base_url = request.build_absolute_uri()
        url = replace_query_param(base_url, self.page_query_param, page_number)
        per_page = self.get_page_size(request) or self.page_size
        url = replace_query_param(url, self.page_size_query_param, per_page)
        return url

    def get_next_link(self):
        if not self.page.has_next():
            return None
        return self._build_url(self.page.next_page_number())

    def get_previous_link(self):
        if not self.page.has_previous():
            return None
        return self._build_url(self.page.previous_page_number())

    def get_paginated_response(self, data):
        return Response({
            "pagination": {
                "total": self.page.paginator.count,
                "per_page": self.get_page_size(self.request) or self.page_size,
                "current_page": self.page.number,
                "last_page": self.page.paginator.num_pages,
                "next_page_url": self.get_next_link(),
                "prev_page_url": self.get_previous_link(),
            },
            "results": data
        })

# ---------------------------
# Inline list serializer
# ---------------------------

class LeaveListSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    leave_type_display = serializers.SerializerMethodField()

    class Meta:
        model = Leave
        fields = [
            'id', 'leave_type', 'leave_type_display', 'reason',
            'date', 'status', 'is_approved', 'informed_status',
            'email_body', 'created_at', 'updated_at', 'user'
        ]

    def get_user(self, obj):
        u = obj.user
        return {
            "id": u.id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
        }

    def get_leave_type_display(self, obj):
        return obj.get_leave_type_display()

class LeaveListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LeaveListSerializer
    pagination_class = AmpecPagination 

    def get_queryset(self):
        user = self.request.user
        qp = self.request.query_params
        qs = Leave.objects.select_related('user').all()

        # -------- Access control + base filtering by USER ID --------
        # ?id=<USER_ID>  (admin can see any; others only themselves)
        user_id_param = qp.get('id')
        uid = parse_int(user_id_param) if user_id_param else None

        if user.username == 'frahman':
            # Admin: if id provided, filter to that user; else all
            if uid is not None:
                qs = qs.filter(user_id=uid)
        else:
            # Non-admin: always restrict to own user_id
            qs = qs.filter(user_id=user.id)

        # -------- Additional filters (columns) --------
        # Specific Leave row: use ?leave_id=<LEAVE_ID>
        leave_id = qp.get('leave_id')
        if leave_id is not None:
            lid = parse_int(leave_id)
            if lid is not None:
                qs = qs.filter(id=lid)
            else:
                qs = qs.none()

        if 'leave_type' in qp:
            qs = qs.filter(leave_type=qp['leave_type'])

        if 'reason' in qp:
            qs = qs.filter(reason__icontains=qp['reason'])

        if 'status' in qp:
            qs = qs.filter(status=qp['status'])

        if 'is_approved' in qp:
            val = parse_bool(qp.get('is_approved'))
            if val is not None:
                qs = qs.filter(is_approved=val)

        if 'informed_status' in qp:
            qs = qs.filter(informed_status__icontains=qp['informed_status'])

        # JSON date filters
        one_date = qp.get('date')
        if one_date:
            qs = qs.filter(date__contains=[one_date])

        many_dates = qp.get('dates')
        if many_dates:
            items = [d.strip() for d in many_dates.split(',') if d.strip()]
            for d in items:
                qs = qs.filter(date__contains=[d])  # include ALL those dates

        # created_at / updated_at ranges
        created_from = qp.get('created_from')
        created_to   = qp.get('created_to')
        updated_from = qp.get('updated_from')
        updated_to   = qp.get('updated_to')

        def _to_dt(s):
            if not s:
                return None
            dt = parse_datetime(s)
            if dt:
                return dt
            d = parse_date(s)
            if d:
                from datetime import datetime, time
                return datetime.combine(d, time.min)
            return None

        from_dt = _to_dt(created_from)
        to_dt   = _to_dt(created_to)
        if from_dt:
            qs = qs.filter(created_at__gte=from_dt)
        if to_dt:
            if to_dt.time().isoformat() == "00:00:00":
                from datetime import datetime, time
                to_dt = datetime.combine(to_dt.date(), time.max)
            qs = qs.filter(created_at__lte=to_dt)

        ufrom_dt = _to_dt(updated_from)
        uto_dt   = _to_dt(updated_to)
        if ufrom_dt:
            qs = qs.filter(updated_at__gte=ufrom_dt)
        if uto_dt:
            if uto_dt.time().isoformat() == "00:00:00":
                from datetime import datetime, time
                uto_dt = datetime.combine(uto_dt.date(), time.max)
            qs = qs.filter(updated_at__lte=uto_dt)

        # Ordering (validated)
        order_by = qp.get('order_by', '-created_at')
        if order_by.lstrip('-') not in ALLOWED_ORDER_FIELDS:
            order_by = '-created_at'
        return qs.order_by(order_by)