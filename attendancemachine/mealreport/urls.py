from django.urls import path
from .views import (
    MealDailyReportListView,
    MealDailyReportDetailView,
    MealDailyUsersView,
    MealDailyAbsenteesView,
    MealPaymentView,
    MealOptOutSummaryView,
)

urlpatterns = [
    # 📊 Get all daily reports (next 7 weekdays)
    path("daily/", MealDailyReportListView.as_view(), name="daily"),

    # 📅 Get single day detailed report
    path("daily/<str:date>/", MealDailyReportDetailView.as_view(), name="daily-detail"),

    # 👥 Get users who are eating on a specific date
    path("daily/<str:date>/users/", MealDailyUsersView.as_view(), name="daily-users"),

    # 🚫 Get absentees (opt-outs + leave)
    path("daily/<str:date>/absentees/", MealDailyAbsenteesView.as_view(), name="daily-absentees"),

    # 💳 Payment management (create/update/delete)
    path("payment/", MealPaymentView.as_view(), name="payment"),

    # 📊 Opt-out summary (for next 7 weekdays)
    path("optouts/", MealOptOutSummaryView.as_view(), name="optouts"),
]
