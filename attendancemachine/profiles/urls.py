# profiles/urls.py

from django.urls import path
from .views import UpdateProfileView

urlpatterns = [
    path('update/', UpdateProfileView.as_view(), name='update_profile'),
]