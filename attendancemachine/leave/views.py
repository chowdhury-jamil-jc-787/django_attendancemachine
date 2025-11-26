from datetime import date as date_cls, datetime

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.shortcuts import get_current_site
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from rest_framework import serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import APIView
from rest_framework import generics

from django.utils.dateparse import parse_datetime, parse_date

from types import SimpleNamespace
from django.shortcuts import redirect

from .models import Leave
from .serializers import LeaveSerializer
from .utils import send_leave_email, correct_grammar_and_paraphrase
from datetime import date, timedelta


from member.models import Member, MemberAssignment
from django.contrib.auth.models import User
from urllib.parse import urlencode
User = get_user_model()

# -------------------------------------------------
# Leave apply + approval-link redirection
# -------------------------------------------------

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
            }, status=201)

        print("❌ Serializer errors:", serializer.errors)
        return Response(serializer.errors, status=400)


class LeaveApprovalView(APIView):
    permission_classes = [AllowAny]  # ✅ allow public access from email

    def get(self, request, pk):
        """
        Redirect to frontend leave review page (no approval/rejection here).
        """
        action = request.GET.get("action")
        leave = Leave.objects.filter(pk=pk).first()
        if not leave:
            raise Http404("Leave not found.")
        frontend_url = f"https://atpldhaka.com/leave-review/{leave.id}?action={action}"
        return redirect(frontend_url)


# -------------------------------------------------
# Optional model resolver (kept if you later need it)
# -------------------------------------------------

def _get_member_model():
    """
    Resolve the Member model from another app safely.
    Priority:
      1) settings.MEMBER_MODEL = "app_label.ModelName"  (e.g., "assign.Member")
      2) fallback common guesses: ("assign", "Member"), ("members", "Member")
    """
    model_path = getattr(settings, "MEMBER_MODEL", None)
    if model_path and "." in model_path:
        app_label, model_name = model_path.rsplit(".", 1)
        Model = apps.get_model(app_label, model_name)
        if Model is not None:
            return Model

    for app_label in ("assign", "members"):
        Model = apps.get_model(app_label, "Member")
        if Model is not None:
            return Model
    return None


# -------------------------------------------------
# Decision: approve/reject (sends requester + team emails)
# -------------------------------------------------

class LeaveDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if request.user.username != "frahman":
            return Response({"error": "You are not authorized to perform this action."}, status=403)

        action = request.data.get("action")
        if action not in ["approve", "reject"]:
            return Response({"error": "Invalid action."}, status=400)

        row = (Leave.objects
               .filter(pk=pk)
               .values("id","leave_type","reason","status","is_approved",
                       "user_id","email_body","informed_status","date","created_at")
               .first())
        if not row:
            raise Http404("Leave not found.")
        if row["status"] != "pending":
            return Response({"message": f"Leave is already {row['status']}."}, status=400)

        new_status = "approved" if action == "approve" else "rejected"
        new_is_approved = (action == "approve")

        raw_date = row.get("date")
        if isinstance(raw_date, str):
            date_list = [raw_date]
        elif isinstance(raw_date, list):
            date_list = raw_date
        else:
            date_list = []

        informed_status_value = row.get("informed_status")
        if new_status == "approved" and date_list:
            try:
                earliest_leave_date = min(date_cls.fromisoformat(d) for d in date_list)
                created_date = row["created_at"].date()
                informed_status_value = "informed" if (earliest_leave_date - created_date).days >= 3 else "uninformed"
            except Exception:
                informed_status_value = "uninformed"

        with transaction.atomic():
            update_kwargs = {
                "status": new_status,
                "is_approved": new_is_approved,
                "updated_at": timezone.now(),
            }
            if new_status == "approved":
                update_kwargs["informed_status"] = informed_status_value
            updated = (Leave.objects.filter(pk=pk, status="pending").update(**update_kwargs))
            if updated == 0:
                return Response({"message": "Leave status already changed."}, status=400)

        User = get_user_model()
        user = User.objects.only("id","email","username","first_name","last_name").get(pk=row["user_id"])

        corrected_reason = (
            correct_grammar_and_paraphrase(row["reason"] or "")
            if row["leave_type"] in ("1st_half","2nd_half")
            else row["reason"]
        )

        display_map = {"full_day": "Full Day", "1st_half": "First Half", "2nd_half": "Second Half"}
        leave_ns = SimpleNamespace(
            id=row["id"],
            leave_type=row["leave_type"],
            reason=row["reason"],
            status=new_status,
            is_approved=new_is_approved,
            email_body=row.get("email_body"),
            informed_status=informed_status_value if new_status == "approved" else row.get("informed_status"),
            date=date_list,
            get_leave_type_display=display_map.get(row["leave_type"], row["leave_type"]),
        )

        # ✅ Notify the applied user (you had removed this block)
        try:
            requester_ctx = {"user": user, "leave": leave_ns, "corrected_reason": corrected_reason}
            requester_subject = f"Your Leave Request Has Been {new_status.upper()}"
            requester_body = render_to_string("leave/leave_decision_email.html", requester_ctx)
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
            requester_msg = EmailMessage(requester_subject, requester_body, from_email=from_email, to=[user.email])
            requester_msg.content_subtype = "html"
            requester_msg.send()
        except Exception as e:
            print(f"❌ Failed to notify user: {str(e)}")

        # Admin notice (HTML)
        try:
            display_name = (user.get_full_name() or user.username)
            admin_ctx = {
                "action": action,
                "display_name": display_name,
                "leave": leave_ns,
                "reason": corrected_reason or row.get("reason", ""),
            }
            admin_subject = f"You have {action}ed a leave request"
            admin_body = render_to_string("leave/admin_decision_email.html", admin_ctx)
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
            admin_msg = EmailMessage(admin_subject, admin_body, from_email=from_email, to=["faisal@ampec.com.au"])
            admin_msg.content_subtype = "html"
            admin_msg.send()
        except Exception as e:
            print(f"❌ Failed to notify admin: {str(e)}")

        # Team notice (on approval)
        if new_status == "approved":
            try:
                team_emails = list(
                    user.members.values_list("email", flat=True)
                        .exclude(email__isnull=True)
                        .exclude(email="")
                        .distinct()
                )
                if not team_emails:
                    Member = _get_member_model()
                    if Member:
                        team_emails = list(
                            Member.objects.filter(user_assignments__user_id=row["user_id"])
                                .values_list("email", flat=True)
                                .exclude(email__isnull=True)
                                .exclude(email="")
                                .distinct()
                        )

                if team_emails:
                    display_name = (user.get_full_name() or user.username)
                    team_subject = f"Team Notice: {display_name}'s leave ({leave_ns.get_leave_type_display})"
                    team_ctx = {
                        "display_name": display_name,
                        "leave": leave_ns,
                        "dates": date_list,
                        "reason": corrected_reason or row.get("reason", ""),
                    }
                    team_body = render_to_string("leave/team_leave_notice.html", team_ctx)
                    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
                    team_msg = EmailMessage(team_subject, team_body, from_email=from_email, to=team_emails)
                    team_msg.content_subtype = "html"
                    team_msg.send()
                else:
                    print(f"ℹ️ No member emails found for user {user.id}; team notice skipped.")
            except Exception as e:
                print(f"❌ Team notification failed: {e}")

        return Response({
            "message": f"Leave has been {new_status}.",
            "informed_status": leave_ns.informed_status
        }, status=200)



# -------------------------------------------------
# Helpers kept inside views.py
# -------------------------------------------------

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


# -------------------------------------------------
# Inline list serializer + list view
# -------------------------------------------------

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
        user_id_param = qp.get('id')
        uid = parse_int(user_id_param) if user_id_param else None

        if user.username == 'frahman':
            if uid is not None:
                qs = qs.filter(user_id=uid)
        else:
            qs = qs.filter(user_id=user.id)

        # -------- Additional filters (columns) --------
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


# -------------------------------------------------
# Summary & manual create
# -------------------------------------------------

class LeaveUserSummaryView(APIView):
    """
    Adds per-user keys:
      - total_leave (period-aware)
      - informed_leave (period-aware)
      - uninformed_leave (period-aware)
      - monthly_breakdown (ALWAYS whole requested year)
      - details (optional, period-aware)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        User = get_user_model()
        user = request.user
        qp = request.query_params

        # ---- period parsing ----
        period = (qp.get('period') or '').lower()  # 'yearly' | 'monthly' | ''
        year = parse_int(qp.get('year')) or datetime.utcnow().year
        month = parse_int(qp.get('month'))
        want_details = bool(parse_bool(qp.get('details')))

        if period == 'monthly' and not month:
            return Response({"error": "month is required when period=monthly (1-12)."}, status=400)

        def date_matches_for_total(iso_str: str) -> bool:
            """Used for total/informed/uninformed (respects period)."""
            try:
                d = date_cls.fromisoformat(iso_str)
            except Exception:
                return False
            if period == 'monthly':
                return d.year == year and d.month == month
            # default or 'yearly'
            return d.year == year

        def month_index_if_in_year(iso_str: str):
            """Used for monthly_breakdown (whole year). Returns 0..11 or None."""
            try:
                d = date_cls.fromisoformat(iso_str)
            except Exception:
                return None
            return (d.month - 1) if d.year == year else None

        # ---- choose users ----
        uid_param = qp.get('id')
        if user.username == 'frahman':
            users_qs = User.objects.select_related('profile').all()
            if uid_param:
                uid = parse_int(uid_param)
                if uid is None:
                    return Response({"error": "id must be an integer."}, status=400)
                users_qs = users_qs.filter(id=uid)
            else:
                # Admin default: hide admin/self and emp_code=="00"
                users_qs = users_qs.exclude(Q(username='frahman') | Q(profile__emp_code='00'))
        else:
            users_qs = User.objects.select_related('profile').filter(id=user.id)

        user_ids = list(users_qs.values_list('id', flat=True))

        # ---- fetch relevant leaves (approved only) ----
        leaves = (Leave.objects
                  .filter(user_id__in=user_ids, status='approved')
                  .values('user_id', 'leave_type', 'reason', 'date', 'informed_status'))

        # ---- accumulators ----
        totals              = {uid: 0.0 for uid in user_ids}  # period-aware
        informed_totals     = {uid: 0.0 for uid in user_ids}
        uninformed_totals   = {uid: 0.0 for uid in user_ids}

        monthly_leave       = {uid: [0.0]*12 for uid in user_ids}  # whole year
        monthly_informed    = {uid: [0.0]*12 for uid in user_ids}
        monthly_uninformed  = {uid: [0.0]*12 for uid in user_ids}

        details_map = {uid: [] for uid in user_ids} if want_details else None

        # ---- iterate all leaves ----
        for r in leaves:
            uid = r['user_id']
            leave_type = r['leave_type']
            reason = r['reason']
            inf_status = (r.get('informed_status') or '').lower()
            is_informed = (inf_status == 'informed')

            dates = r.get('date') or []
            if isinstance(dates, str):
                dates = [dates]

            unit = 1.0 if leave_type == 'full_day' else 0.5

            for iso in dates:
                # period-aware totals
                if date_matches_for_total(iso):
                    totals[uid] += unit
                    if is_informed:
                        informed_totals[uid] += unit
                    else:
                        uninformed_totals[uid] += unit
                    if want_details:
                        details_map[uid].append({
                            "date": iso,
                            "leave_type": leave_type,
                            "reason": reason
                        })

                # monthly breakdown (whole year)
                mi = month_index_if_in_year(iso)
                if mi is not None:
                    monthly_leave[uid][mi] += unit
                    if is_informed:
                        monthly_informed[uid][mi] += unit
                    else:
                        monthly_uninformed[uid][mi] += unit

        if want_details:
            for uid in details_map:
                details_map[uid].sort(key=lambda x: x["date"])

        month_names = [
            "January","February","March","April","May","June",
            "July","August","September","October","November","December"
        ]

        period_info = {"type": "monthly", "year": year, "month": month} if period == 'monthly' else {
            "type": "yearly", "year": year
        }

        results = []
        for u in users_qs:
            profile = getattr(u, 'profile', None)
            emp_code = getattr(profile, 'emp_code', None) if profile else None
            profile_img = getattr(profile, 'profile_img', None)
            profile_img_url = profile_img.url if profile_img else None

            monthly_breakdown = [
                {
                    "month": month_names[i],
                    "leave": float(monthly_leave[u.id][i]),
                    "informed_leave": float(monthly_informed[u.id][i]),
                    "uninformed_leave": float(monthly_uninformed[u.id][i]),
                }
                for i in range(12)
            ]

            payload = {
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "email": u.email,
                "emp_code": emp_code,
                "profile_img": profile_img_url,
                "period": period_info,
                "total_leave": float(totals.get(u.id, 0.0)),
                "informed_leave": float(informed_totals.get(u.id, 0.0)),
                "uninformed_leave": float(uninformed_totals.get(u.id, 0.0)),
                "monthly_breakdown": monthly_breakdown,
            }
            if want_details:
                payload["details"] = details_map.get(u.id, [])

            results.append(payload)

        return Response({"results": results}, status=200)


# ---- Serializer for manual creation (inline) ----
class ManualLeaveCreateSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    leave_type = serializers.ChoiceField(choices=['full_day', '1st_half', '2nd_half'])
    # array of YYYY-MM-DD strings
    date = serializers.ListField(
        child=serializers.DateField(format='%Y-%m-%d', input_formats=['%Y-%m-%d']),
        allow_empty=False
    )
    reason = serializers.CharField(allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=['pending', 'approved', 'rejected'], required=False)
    informed_status = serializers.ChoiceField(choices=['informed', 'uninformed'], required=False)

    def validate(self, attrs):
        user_id = attrs['user_id']
        leave_type = attrs['leave_type']
        dates = attrs['date']

        # normalize & dedupe
        dates = sorted({d.isoformat() for d in dates})
        if leave_type in ('1st_half', '2nd_half') and len(dates) != 1:
            raise serializers.ValidationError("Half-day leave must have exactly one date.")
        if leave_type == 'full_day' and len(dates) < 1:
            raise serializers.ValidationError("Full-day leave must have at least one date.")

        # user must exist
        User = get_user_model()
        if not User.objects.filter(id=user_id).exists():
            raise serializers.ValidationError("user_id not found.")

        # overlap check against pending/approved for that user
        qs = Leave.objects.filter(user_id=user_id, status__in=['pending', 'approved'])
        for d in dates:
            if qs.filter(date__contains=[d]).exists():
                raise serializers.ValidationError(f"Leave already exists on {d} for this user.")

        attrs['date'] = dates  # keep normalized
        return attrs


class ManualLeaveCreateView(APIView):
    """
    POST /api/leave/manual/
    Only 'frahman' can create manual leaves.

    Body:
    {
      "user_id": 72,
      "leave_type": "full_day",        // "full_day" | "1st_half" | "2nd_half"
      "date": ["2025-08-20","2025-08-21"],  // array of YYYY-MM-DD (half-day => exactly 1)
      "reason": "Family event",
      "status": "approved",            // optional; default "approved" for manual
      "informed_status": "informed"    // optional; if omitted, computed automatically
    }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # gate: only frahman
        if request.user.username != 'frahman':
            return Response({"error": "You are not authorized to perform this action."}, status=403)

        ser = ManualLeaveCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=400)

        data = ser.validated_data
        user_id = data['user_id']
        leave_type = data['leave_type']
        dates = data['date']  # list of ISO strings
        reason = data.get('reason', '') or ''

        # Default status for manual create: approved (you can change to pending if you like)
        status_value = data.get('status') or 'approved'
        is_approved = (status_value == 'approved')

        # Informed status: use provided or compute from "now" vs earliest date
        informed = data.get('informed_status')
        if not informed:
            try:
                earliest = min(date_cls.fromisoformat(d) for d in dates)
                created = timezone.localdate()  # today's date in server timezone
                informed = 'informed' if (earliest - created).days >= 3 else 'uninformed'
            except Exception:
                informed = 'uninformed'

        # create
        User = get_user_model()
        target_user = User.objects.get(id=user_id)

        with transaction.atomic():
            leave = Leave.objects.create(
                user=target_user,
                leave_type=leave_type,
                reason=reason,
                date=dates,                   # JSON list
                status=status_value,
                is_approved=is_approved,
                informed_status=informed
            )

        # minimal response
        return Response({
            "message": "Manual leave created.",
            "data": {
                "id": leave.id,
                "user_id": leave.user_id,
                "leave_type": leave.leave_type,
                "date": leave.date,
                "reason": leave.reason,
                "status": leave.status,
                "is_approved": leave.is_approved,
                "informed_status": leave.informed_status,
                "created_at": leave.created_at,
            }
        }, status=201)










class TeamApprovedLeaveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        qp = request.query_params

        page = int(qp.get("page", 1))
        per_page = int(qp.get("per_page", 15))
        offset = (page - 1) * per_page

        # ---------------------------------------------------
        # 1) admin → see all approved leaves
        # ---------------------------------------------------
        if user.username == "frahman":
            qs = Leave.objects.filter(status="approved")

        else:
            # ---------------------------------------------------
            # 2) normal user → only team members' approved leaves
            # ---------------------------------------------------
            team_user_ids = list(
                User.objects.filter(
                    member_assignments__member__user_assignments__user=user
                )
                .exclude(id=user.id)          # exclude self
                .exclude(profile__emp_code="00")
                .values_list("id", flat=True)
                .distinct()
            )

            qs = Leave.objects.filter(
                status="approved",
                user_id__in=team_user_ids
            )

        # optional filters
        start_date = qp.get("start_date")
        end_date = qp.get("end_date")

        if start_date:
            qs = qs.filter(date__contains=[start_date])
        if end_date:
            qs = qs.filter(date__contains=[end_date])

        # count for pagination
        total = qs.count()

        # pagination slice
        rows = qs.order_by("-created_at")[offset: offset + per_page]

        # ---------------------------------------------------
        # build result list manually
        # ---------------------------------------------------
        results = []
        for row in rows:
            u = row.user
            profile = getattr(u, "profile", None)
            emp_code = getattr(profile, "emp_code", None)
            profile_img = profile.profile_img.url if profile and profile.profile_img else None

            results.append({
                "id": row.id,
                "emp_code": emp_code,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "email": u.email,
                "leave_type": row.leave_type,
                "reason": row.reason,
                "date": row.date,
                "status": row.status,
                "informed_status": row.informed_status,
                "is_approved": row.is_approved,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "profile_img": profile_img,
            })

        # ---------------------------------------------------
        # pagination URLs
        # ---------------------------------------------------
        base_url = request.build_absolute_uri(request.path)
        params = qp.dict()
        params["per_page"] = per_page

        def url_for(p):
            params["page"] = p
            return f"{base_url}?{urlencode(params)}"

        last_page = (total + per_page - 1) // per_page

        pagination = {
            "total": total,
            "per_page": per_page,
            "current_page": page,
            "last_page": last_page,
            "next_page_url": url_for(page + 1) if page < last_page else None,
            "prev_page_url": url_for(page - 1) if page > 1 else None,
        }

        return Response({
            "pagination": pagination,
            "results": results
        })
    






class LeaveCalendarView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # -----------------------------------------
        # 1) AUTO-DETECT MONTH IF NOT PROVIDED
        # -----------------------------------------
        month_str = request.query_params.get("month")

        if not month_str:
            today = date.today()
            month_str = today.strftime("%Y-%m")  # example: "2025-02"

        # Validate month format
        try:
            year, month = map(int, month_str.split("-"))
            first_day = date(year, month, 1)
        except Exception:
            return Response({"error": "Invalid month format. Use YYYY-MM"}, status=400)

        # Calculate last day of the month
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)

        # -----------------------------------------
        # 2) DETERMINE VISIBLE USERS
        # -----------------------------------------

        # Admin → see all approved leaves
        if user.username == "frahman":
            visible_user_ids = list(
                User.objects.exclude(profile__emp_code="00")
                .values_list("id", flat=True)
            )

        else:
            # Normal user → See team + self

            # Step A: Find member_ids where this user is assigned
            member_ids = list(
                MemberAssignment.objects.filter(user=user)
                .values_list("member_id", flat=True)
            )

            # Step B: Get member emails (safe Python list → avoids collation issue)
            member_emails = list(
                Member.objects.filter(id__in=member_ids)
                .values_list("email", flat=True)
            )

            # Step C: Get User IDs who have these emails
            employee_user_ids = list(
                User.objects.filter(email__in=member_emails)
                .exclude(profile__emp_code="00")
                .values_list("id", flat=True)
            )

            visible_user_ids = set(employee_user_ids)
            visible_user_ids.add(user.id)

        # -----------------------------------------
        # 3) FETCH APPROVED LEAVES
        # -----------------------------------------

        leaves = Leave.objects.filter(
            status="approved",
            user_id__in=visible_user_ids
        )

        # Filter rows by month because dates are inside JSONField
        filtered = []
        for lv in leaves:
            for d in lv.date:
                try:
                    d_date = date.fromisoformat(d)
                except:
                    continue
                if first_day <= d_date <= last_day:
                    filtered.append((lv, d_date))

        # -----------------------------------------
        # 4) BUILD FULL MONTH CALENDAR
        # -----------------------------------------

        days = []
        current = first_day

        while current <= last_day:
            days.append({
                "date": current.isoformat(),
                "leaves": []
            })
            current += timedelta(days=1)

        index_map = {d["date"]: d for d in days}

        # -----------------------------------------
        # 5) FILL LEAVES INTO CALENDAR DAYS
        # -----------------------------------------

        for lv, d_date in filtered:
            u = lv.user
            profile = getattr(u, "profile", None)
            emp_code = getattr(profile, "emp_code", None)

            entry = {
                "user_id": u.id,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "emp_code": emp_code,
                "leave_type": lv.leave_type,
                "reason": lv.reason,
                "informed_status": lv.informed_status,
            }

            index_map[d_date.isoformat()]["leaves"].append(entry)

        # -----------------------------------------
        # 6) RETURN FINAL RESPONSE
        # -----------------------------------------

        return Response({
            "month": month_str,
            "days": days
        })