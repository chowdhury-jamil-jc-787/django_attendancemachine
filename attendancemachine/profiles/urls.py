# profiles/urls.py

from django.urls import path
from .views import UpdateProfileView, RetrieveProfileView

urlpatterns = [
    path('update/', UpdateProfileView.as_view(), name='update_profile'),
    path('me/', RetrieveProfileView.as_view(), name='my_profile'),
]