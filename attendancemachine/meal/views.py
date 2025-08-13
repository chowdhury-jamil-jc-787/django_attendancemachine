# meal/views.py
from rest_framework import viewsets, permissions
from .models import Meal
from .serializers import MealSerializer
from rest_framework import status
from rest_framework.response import Response

class MealViewSet(viewsets.ModelViewSet):
    """
    CRUD for Meal:
      - GET    /api/meal/           -> list
      - POST   /api/meal/           -> create
      - GET    /api/meal/{id}/      -> retrieve
      - PUT    /api/meal/{id}/      -> full update
      - PATCH  /api/meal/{id}/      -> partial update
      - DELETE /api/meal/{id}/      -> delete
    """
    queryset = Meal.objects.all().order_by('id')
    serializer_class = MealSerializer
    permission_classes = [permissions.IsAuthenticated]

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {"message": "Meal deleted successfully", "id": instance.id},
            status=status.HTTP_200_OK
        )
