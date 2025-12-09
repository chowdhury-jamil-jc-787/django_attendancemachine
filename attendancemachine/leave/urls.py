from django.urls import path
from .views import (
    LeaveRequestView,
    LeaveApprovalView,
    LeaveDecisionView,
    LeaveListView,
    LeaveUserSummaryView,
    ManualLeaveCreateView,
    TeamApprovedLeaveView,
    LeaveCalendarView,
    UpcomingLeaveView,
    LeaveCancelRequestView,
    # LeaveCancelDecisionView,   # ❌ not needed for auto-cancel
    # LeaveCancelReviewBackendView,  # ❌ remove, not implemented
    AdminFutureLeaveCancelView,
)

urlpatterns = [
    # -------------------------------
    # APPLY LEAVE
    # -------------------------------
    path('apply/', LeaveRequestView.as_view(), name='leave-apply'),

    # -------------------------------
    # APPROVE / REJECT LEAVE (normal leave flow)
    # -------------------------------
    path('approve/<int:pk>/', LeaveApprovalView.as_view(), name='leave-approval'),
    path('decision/<int:pk>/', LeaveDecisionView.as_view(), name='leave-decision'),

    # -------------------------------
    # LIST + SUMMARY
    # -------------------------------
    path('list/', LeaveListView.as_view(), name='leave-list'),
    path('summary/', LeaveUserSummaryView.as_view(), name='leave-summary'),

    # -------------------------------
    # MANUAL LEAVE CREATION (admin only)
    # -------------------------------
    path('manual/', ManualLeaveCreateView.as_view(), name='leave-manual-create'),

    # -------------------------------
    # TEAM LEAVES + CALENDAR + UPCOMING
    # -------------------------------
    path('team-approved/', TeamApprovedLeaveView.as_view(), name='team-approved'),
    path('calendar/', LeaveCalendarView.as_view(), name='leave-calendar'),
    path('upcoming-leaves/', UpcomingLeaveView.as_view(), name='upcoming-leaves'),

    # -------------------------------
    # AUTO CANCEL REQUEST (user)
    # -------------------------------
    path('cancel-request/<int:pk>/', LeaveCancelRequestView.as_view(), name='leave-cancel-request'),

    # If in future you want manual admin decision for cancel again,
    # you can re-enable this:
    # path('cancel-decision/<int:pk>/', LeaveCancelDecisionView.as_view(), name='leave-cancel-decision'),

    path('admin-cancel-future/<int:pk>/', AdminFutureLeaveCancelView.as_view(),
        name='leave-admin-cancel-future'),
]
