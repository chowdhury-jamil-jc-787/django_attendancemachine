# profiles/urls.py

from django.urls import path
from .views import UpdateProfileView, RetrieveProfileView, resized_image_view

urlpatterns = [
    path('update/', UpdateProfileView.as_view(), name='update_profile'),
    path('me/', RetrieveProfileView.as_view(), name='my_profile'),
    path('resize/', resized_image_view, name='resize_image'),  # ← added route
]