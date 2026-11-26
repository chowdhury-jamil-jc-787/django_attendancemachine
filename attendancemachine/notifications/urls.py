
# notifications/urls.py

from django.urls import path

from .views import (
    SaveDeviceTokenView,
    DeleteDeviceTokenView,
    ProcessScheduledAttendanceNotificationsView,
)


urlpatterns = [
    # ==========================================
    # EXISTING FIREBASE DEVICE TOKEN APIs
    # ==========================================

    path(
        "device-token/save/",
        SaveDeviceTokenView.as_view(),
        name="device-token-save",
    ),

    path(
        "device-token/delete/",
        DeleteDeviceTokenView.as_view(),
        name="device-token-delete",
    ),

    # ==========================================
    # NEW SCHEDULED ATTENDANCE NOTIFICATIONS
    # ==========================================

    # Keep the existing cPanel cron URL.
    # It now processes scheduled reminders
    # instead of sending notifications for
    # every attendance punch.

    path(
        "process-punches/",
        ProcessScheduledAttendanceNotificationsView.as_view(),
        name="process-scheduled-attendance",
    ),
]
