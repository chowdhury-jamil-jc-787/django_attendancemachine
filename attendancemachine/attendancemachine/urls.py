"""
URL configuration for the attendancemachine project.

Existing application routes are preserved. Device-session management
and device-bound JWT endpoints are added below.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from myapp.views import (
    ChangePasswordView,
    DashboardView,
    DeviceSessionsView,
    DeviceTokenObtainPairView,
    DeviceTokenRefreshView,
    LoginView,
    LogoutAllDevicesView,
    LogoutDeviceView,
    LogoutOtherDevicesView,
    LogoutView,
    RegisterView,
)
from seatplan.views import SeatPlanView


urlpatterns = [
    # Django Admin
    path('admin/', admin.site.urls),

    # Authentication (existing endpoints)
    path(
        'api/auth/register/',
        RegisterView.as_view(),
        name='auth_register'
    ),
    path(
        'api/auth/login/',
        LoginView.as_view(),
        name='auth_login'
    ),
    path(
        'api/auth/logout/',
        LogoutView.as_view(),
        name='auth_logout'
    ),
    path(
        'api/auth/change-password/',
        ChangePasswordView.as_view(),
        name='auth_change_password'
    ),

    # Device-session management (new endpoints)
    path(
        'api/auth/sessions/',
        DeviceSessionsView.as_view(),
        name='auth_device_sessions'
    ),
    path(
        'api/auth/sessions/<uuid:session_id>/',
        LogoutDeviceView.as_view(),
        name='auth_logout_device'
    ),
    path(
        'api/auth/logout-others/',
        LogoutOtherDevicesView.as_view(),
        name='auth_logout_other_devices'
    ),
    path(
        'api/auth/logout-all/',
        LogoutAllDevicesView.as_view(),
        name='auth_logout_all_devices'
    ),

    # Dashboard
    path(
        'api/dashboard/',
        DashboardView.as_view(),
        name='dashboard'
    ),

    # JWT authentication, now tied to revocable device sessions
    path(
        'api/token/',
        DeviceTokenObtainPairView.as_view(),
        name='token_obtain_pair'
    ),
    path(
        'api/token/refresh/',
        DeviceTokenRefreshView.as_view(),
        name='token_refresh'
    ),

    # Employee App
    path(
        'api/employee/',
        include('employee.urls')
    ),

    # Profiles App
    path(
        'api/profiles/',
        include('profiles.urls')
    ),

    # Leave Management
    path(
        'api/leave/',
        include('leave.urls')
    ),

    # Meal Management
    path(
        'api/',
        include('meal.urls')
    ),

    # Member Assignments
    path(
        'api/assign/',
        include('member.urls')
    ),

    # Meal Reports
    path(
        'api/mealreport/',
        include('mealreport.urls')
    ),

    # bKash Gateway
    path(
        'api/bkash/',
        include('bkashgateway.urls')
    ),

    # Seat Plan
    path(
        'seatplan/',
        include('seatplan.urls')
    ),
    path(
        'api/seatplan/',
        SeatPlanView.as_view()
    ),

    # Notifications
    path(
        'api/notifications/',
        include('notifications.urls')
    ),

    # Attendance Monitor
    path(
        'api/attendance-monitor/',
        include('attendance_monitor.urls')
    ),
]


# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )
