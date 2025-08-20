# meal/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MealViewSet, OverrideMealView, OverrideMealDetailView

router = DefaultRouter()
router.register(r'meal', MealViewSet, basename='meal')

urlpatterns = [
    path('meal/override/', OverrideMealView.as_view(), name='meal-override'),              # POST/DELETE
    path('meal/override/<int:pk>/', OverrideMealDetailView.as_view(), name='meal-override-detail'),  # GET
    path('', include(router.urls)),
]
