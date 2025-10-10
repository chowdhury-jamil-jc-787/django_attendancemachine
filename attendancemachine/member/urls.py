from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MemberViewSet, UserMembersView, UsersMembersView

router = DefaultRouter()
router.register(r'members', MemberViewSet, basename='member')

urlpatterns = [
    path('', include(router.urls)),
    path('users/<int:user_id>/members/', UserMembersView.as_view(), name='user-members'),   # existing
    path('users/members/', UsersMembersView.as_view(), name='users-members'),               # NEW (all users)
]
