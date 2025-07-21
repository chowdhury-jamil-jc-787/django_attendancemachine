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
                if leave.leave_type == 'half_day'
                else leave.reason
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
        """
        Only 'frahman' can approve or reject a leave via frontend.
        """
        if request.user.username != "frahman":
            return Response({"error": "You are not authorized to perform this action."}, status=403)

        action = request.data.get("action")
        if action not in ["approve", "reject"]:
            return Response({"error": "Invalid action."}, status=400)

        leave = get_object_or_404(Leave, pk=pk)

        if leave.status != "pending":
            return Response({"message": f"Leave is already {leave.status}."}, status=400)

        # Update leave status
        if action == "approve":
            leave.status = "approved"
            leave.is_approved = True
        else:
            leave.status = "rejected"
            leave.is_approved = False

        leave.save()

        # Notify the requester
        try:
            corrected_reason = (
                correct_grammar_and_paraphrase(leave.reason or "")
                if leave.leave_type == 'half_day'
                else leave.reason
            )

            context = {
                'user': leave.user,
                'leave': leave,
                'corrected_reason': corrected_reason,
            }
            subject = f"Your Leave Request Has Been {leave.status.upper()}"
            body = render_to_string("leave/leave_decision_email.html", context)

            email = EmailMessage(subject, body, to=[leave.user.email])
            email.content_subtype = "html"
            email.send()
        except Exception as e:
            print(f"❌ Failed to notify user: {str(e)}")

        # Notify admin (optional)
        try:
            admin_email = EmailMessage(
                subject=f"You have {action}ed a leave request",
                body=f"You have {action}ed {leave.user.get_full_name()}'s leave request.",
                to=["jamil@ampec.com.au"]
            )
            admin_email.send()
        except Exception as e:
            print(f"❌ Failed to notify admin: {str(e)}")

        return Response({"message": f"Leave has been {leave.status}."}, status=200)
