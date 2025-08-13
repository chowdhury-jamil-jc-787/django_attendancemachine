from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404, redirect
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
from django.http import HttpResponse
from django.utils.html import escape

from .serializers import LeaveSerializer
from .models import Leave
from .utils import send_leave_email, correct_grammar_and_paraphrase


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

        # Fetch only the fields that exist in DB now (avoid old missing columns)
        row = Leave.objects.filter(pk=pk).values(
            "id", "leave_type", "reason", "status", "is_approved", "user_id", "email_body", "informed_status", "date"
        ).first()
        if not row:
            raise Http404("Leave not found.")

        if row["status"] != "pending":
            return Response({"message": f"Leave is already {row['status']}."}, status=400)

        new_status = "approved" if action == "approve" else "rejected"
        new_is_approved = (action == "approve")

        # Update atomically without loading full model instance
        with transaction.atomic():
            updated = (Leave.objects
                .filter(pk=pk, status="pending")
                .update(status=new_status, is_approved=new_is_approved, updated_at=timezone.now()))
            if updated == 0:
                return Response({"message": "Leave status already changed."}, status=400)

        # Load the user (only required fields)
        User = get_user_model()
        user = User.objects.only("id", "email", "username", "first_name", "last_name").get(pk=row["user_id"])

        # Prepare data for email/template without touching missing fields
        # Grammar correction only for half-day types in your new scheme
        corrected_reason = (
            correct_grammar_and_paraphrase(row["reason"] or "")
            if row["leave_type"] in ("1st_half", "2nd_half")
            else row["reason"]
        )

        # Make a simple object with attributes accessible in the template
        from types import SimpleNamespace
        # Provide a display string since templates used get_leave_type_display previously
        display_map = {"full_day": "Full Day", "1st_half": "First Half", "2nd_half": "Second Half"}
        leave_ns = SimpleNamespace(
            id=row["id"],
            leave_type=row["leave_type"],
            reason=row["reason"],
            status=new_status,
            is_approved=new_is_approved,
            email_body=row.get("email_body"),
            informed_status=row.get("informed_status"),
            date=row.get("date") or [],  # list of ISO dates
            get_leave_type_display=display_map.get(row["leave_type"], row["leave_type"]),
        )

        # Render and send emails
        try:
            context = {"user": user, "leave": leave_ns, "corrected_reason": corrected_reason}
            subject = f"Your Leave Request Has Been {new_status.upper()}"
            body = render_to_string("leave/leave_decision_email.html", context)
            email = EmailMessage(subject, body, to=[user.email])
            email.content_subtype = "html"
            email.send()
        except Exception as e:
            print(f"❌ Failed to notify user: {str(e)}")

        try:
            admin_email = EmailMessage(
                subject=f"You have {action}ed a leave request",
                body=f"You have {action}ed {user.get_full_name() or user.username}'s leave request.",
                to=["jamil@ampec.com.au"]
            )
            admin_email.send()
        except Exception as e:
            print(f"❌ Failed to notify admin: {str(e)}")

        return Response({"message": f"Leave has been {new_status}."}, status=200)
