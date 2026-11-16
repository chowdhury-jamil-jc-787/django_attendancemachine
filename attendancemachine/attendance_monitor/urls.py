from django.urls import path
from .views import AttendanceMonitorView

app_name = "attendance_monitor"

urlpatterns = [
    path(
        "",
        AttendanceMonitorView.as_view(),
        name="attendance-report",
    ),
]
