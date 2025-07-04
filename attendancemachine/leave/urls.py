from django.urls import path
from .views import LeaveRequestView, LeaveApprovalView

urlpatterns = [
    path('apply/', LeaveRequestView.as_view(), name='leave-apply'),
    path('approve/<int:pk>/', LeaveApprovalView.as_view(), name='leave-approval'),
]
