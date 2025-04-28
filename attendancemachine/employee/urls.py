# employee/urls.py
from django.urls import path
from .views import EmployeeInfoView, DailyFirstPunchesView

urlpatterns = [
    path('info/', EmployeeInfoView.as_view()),
    path('daily-punches/', DailyFirstPunchesView.as_view()),
]