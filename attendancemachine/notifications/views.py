# notifications/views.py

import logging
import secrets

from django.conf import settings
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UserDeviceToken
from .serializers import UserDeviceTokenSerializer
from .services import send_attendance_reminders


logger = logging.getLogger(__name__)


class SaveDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = UserDeviceTokenSerializer(
            data=request.data,
            context={"request": request}
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "success": True,
                "message": "Device token saved successfully.",
                "device": serializer.data
            }, status=201)

        return Response({
            "success": False,
            "message": "Could not save device token.",
            "errors": serializer.errors
        }, status=400)


class DeleteDeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("token")

        if not token:
            return Response({
                "success": False,
                "message": "token is required."
            }, status=400)

        UserDeviceToken.objects.filter(
            user=request.user,
            token=token
        ).update(is_active=False)

        return Response({
            "success": True,
            "message": "Device token removed successfully."
        })


@method_decorator(never_cache, name="dispatch")
class ProcessScheduledAttendanceNotificationsView(APIView):
    """Secure entrypoint for the cPanel attendance-reminder cron job.

    The existing cPanel wget request uses GET and the legacy path
    /api/notifications/process-punches/. A POST method is also provided
    to allow switching to a header-based secret later.

    This view is NOT user-authenticated because cron has no JWT. It requires
    a separate ATTENDANCE_CRON_SECRET set in Django settings, and refuses
    all requests if that setting is absent.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return self._process(request)

    def post(self, request, *args, **kwargs):
        return self._process(request)

    def _process(self, request):
        expected_secret = getattr(settings, "ATTENDANCE_CRON_SECRET", "")

        # Fail closed. Never fall back to the old publicly exposed secret.
        if not isinstance(expected_secret, str) or not expected_secret:
            logger.error("Attendance cron secret is not configured.")
            return Response(
                {"success": False, "message": "Cron service is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Header is preferred; query parameter supports the current wget job.
        supplied_secret = (
            request.headers.get("X-Attendance-Cron-Secret")
            or request.query_params.get("secret", "")
        )
        if not isinstance(supplied_secret, str) or not secrets.compare_digest(
            supplied_secret, expected_secret
        ):
            return Response(
                {"success": False, "message": "Forbidden."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            # This replaces the old per-punch alert; duplicate reminders are
            # controlled by AttendanceNotificationEvent in the service layer.
            send_attendance_reminders()
        except Exception:
            logger.exception("Attendance cron processing failed.")
            return Response(
                {"success": False, "message": "Attendance processing failed."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "success": True,
                "message": "Scheduled attendance notification processing completed.",
            },
            status=status.HTTP_200_OK,
        )
