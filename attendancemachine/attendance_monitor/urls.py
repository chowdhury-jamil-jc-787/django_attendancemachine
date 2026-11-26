
from django.urls import path

from .views import (
    AttendanceMonitorView,
    HomeOfficeView,
    HomeOfficeDetailView,
)


app_name = "attendance_monitor"


urlpatterns = [
    # =====================================
    # EXISTING ATTENDANCE MONITOR
    # =====================================

    path(
        "",
        AttendanceMonitorView.as_view(),
        name="attendance-report",
    ),

    # =====================================
    # HOME OFFICE MANAGEMENT
    # =====================================

    # List and assign Home Office
    path(
        "home-office/",
        HomeOfficeView.as_view(),
        name="home-office-list-create",
    ),

    # Cancel a Home Office assignment
    path(
        "home-office/<int:pk>/",
        HomeOfficeDetailView.as_view(),
        name="home-office-detail",
    ),
]
