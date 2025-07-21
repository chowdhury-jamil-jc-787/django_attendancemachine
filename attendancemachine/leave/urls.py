from django.urls import path
from .views import LeaveRequestView, LeaveApprovalView, LeaveDecisionView

urlpatterns = [
    path('apply/', LeaveRequestView.as_view(), name='leave-apply'),
    path('approve/<int:pk>/', LeaveApprovalView.as_view(), name='leave-approval'),
    path('decision/<int:pk>/', LeaveDecisionView.as_view(), name='leave-decision'),  # ✅ New secure API
]