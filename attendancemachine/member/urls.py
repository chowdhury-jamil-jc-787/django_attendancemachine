from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    MemberViewSet,
    UserMembersView,
    UsersMembersView,
    UserAssignMemberView,   # 👈 ADD THIS
)

router = DefaultRouter()
router.register(r'members', MemberViewSet, basename='member')

urlpatterns = [
    path('', include(router.urls)),

    # users → members (list)
    path(
        'users/<int:user_id>/members/',
        UserMembersView.as_view(),
        name='user-members'
    ),

    # users → assign member / sign-in (NEW)
    path(
        'users/<int:user_id>/assign-member/',
        UserAssignMemberView.as_view(),
        name='user-assign-member'
    ),

    # all users with members
    path(
        'users/members/',
        UsersMembersView.as_view(),
        name='users-members'
    ),
]
