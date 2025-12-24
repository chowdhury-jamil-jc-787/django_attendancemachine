# seatplan/urls.py
from django.urls import path
from .views import seatplan_page

urlpatterns = [
    path("", seatplan_page, name="seatplan_page"),
]
