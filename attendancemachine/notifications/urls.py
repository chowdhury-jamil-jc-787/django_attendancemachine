# notifications/urls.py

from django.urls import path
from .views import SaveDeviceTokenView, DeleteDeviceTokenView

urlpatterns = [
    path("device-token/save/", SaveDeviceTokenView.as_view(), name="device-token-save"),
    path("device-token/delete/", DeleteDeviceTokenView.as_view(), name="device-token-delete"),
]