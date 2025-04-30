# employee/urls.py
from django.urls import path
from .views import EmployeeInfoView, DailyFirstPunchesView, AttendanceSummaryReport

urlpatterns = [
    path('info/', EmployeeInfoView.as_view()),
    path('daily-punches/', DailyFirstPunchesView.as_view()),
    path('attendance-summary/', AttendanceSummaryReport.as_view()),  # ✅ New function
]
