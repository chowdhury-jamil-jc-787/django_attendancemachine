
"""
URL configuration for attendancemachine project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

from myapp.views import (
    RegisterView,
    LoginView,
    DashboardView,
    LogoutView,
    ChangePasswordView,
)

from seatplan.views import SeatPlanView

from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from django.conf import settings
from django.conf.urls.static import static


urlpatterns = [
    # Django Admin
    path('admin/', admin.site.urls),

    # Authentication
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

    # Dashboard
    path(
        'api/dashboard/',
        DashboardView.as_view(),
        name='dashboard'
    ),

    # JWT Authentication
    path(
        'api/token/',
        TokenObtainPairView.as_view(),
        name='token_obtain_pair'
    ),
    path(
        'api/token/refresh/',
        TokenRefreshView.as_view(),
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

    # NEW: Attendance Monitor
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
