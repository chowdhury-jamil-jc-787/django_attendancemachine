from django.urls import path
from . import views

urlpatterns = [
    path("pay/", views.bkash_pay, name="bkash_pay"),
    path("callback/", views.bkash_callback, name="bkash_callback"),
    path("status/", views.bkash_status, name="bkash_status"),
]
