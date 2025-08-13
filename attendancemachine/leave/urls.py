from django.urls import path
from .views import LeaveRequestView, LeaveApprovalView, LeaveDecisionView, LeaveListView, LeaveUserSummaryView, ManualLeaveCreateView

urlpatterns = [
    path('apply/', LeaveRequestView.as_view(), name='leave-apply'),
    path('approve/<int:pk>/', LeaveApprovalView.as_view(), name='leave-approval'),
    path('decision/<int:pk>/', LeaveDecisionView.as_view(), name='leave-decision'),  # ✅ New secure API
    path('list/', LeaveListView.as_view(), name='leave-list'),  # ✅ NEW
    path('summary/', LeaveUserSummaryView.as_view(), name='leave-summary'),
    path('manual/', ManualLeaveCreateView.as_view(), name='leave-manual-create'),
]