from django.urls import path
from .views import MealDailyReportListView, MealDailyReportDetailView, MealPaymentView, MealDailyUsersView

urlpatterns = [
    path("daily/", MealDailyReportListView.as_view(), name="mealreport-daily"),
    path("daily/<date>/", MealDailyReportDetailView.as_view(), name="mealreport-daily-detail"),
    path("daily/<date>/users/", MealDailyUsersView.as_view(), name="mealreport-daily-users"),  # ✅ NEW
    path("payment/", MealPaymentView.as_view(), name="mealreport-payment"),
]