# meal/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MealViewSet, OverrideMealView, OverrideMealDetailView, CookRecordGenerateView, CookRecordDetailView, CookRecordFinalizeTodayView, MealOptOutListCreateView, MealOptOutDetailView

router = DefaultRouter()
router.register(r'meal', MealViewSet, basename='meal')

urlpatterns = [
    path('meal/opt-outs/', MealOptOutListCreateView.as_view(), name='meal-optout-list-create'),
    path('meal/opt-outs/<int:pk>/', MealOptOutDetailView.as_view(), name='meal-optout-detail'),
    path('cook-records/generate/', CookRecordGenerateView.as_view(), name='cook-record-generate'),
    path('cook-records/', CookRecordDetailView.as_view(), name='cook-record-detail'),
    path('cook-records/finalize-today/', CookRecordFinalizeTodayView.as_view(), name='cook-record-finalize-today'),
    path('meal/override/', OverrideMealView.as_view(), name='meal-override'),              # POST/DELETE
    path('meal/override/<int:pk>/', OverrideMealDetailView.as_view(), name='meal-override-detail'),  # GET
    path('', include(router.urls)),
]
