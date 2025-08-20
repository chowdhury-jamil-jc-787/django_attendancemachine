# meal/views.py
from django.utils.dateparse import parse_date
from django.db import IntegrityError
from rest_framework import viewsets, permissions, status, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import RetrieveAPIView

from .models import Meal, MealOverride
from .serializers import MealSerializer


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


class OverrideMealView(APIView):
    """
    Create/update or delete a MealOverride for a specific DATE.
    Only user 'frahman' can create/update/delete overrides.

    - GET    /api/meal/override/            -> list all overrides
    - POST   /api/meal/override/            -> create/update by date
        body: { "date": "YYYY-MM-DD", "item": "...", "price": 123.45 }
    - DELETE /api/meal/override/            -> delete by date
        body: { "date": "YYYY-MM-DD" }
    """
    permission_classes = [permissions.IsAuthenticated]

    # ✅ ADDED: list all overrides
    def get(self, request):
        overrides = MealOverride.objects.all().order_by('-date')
        data = MealOverrideSerializer(overrides, many=True).data
        return Response(data, status=status.HTTP_200_OK)

    def post(self, request):
        # Only 'frahman' can create/update overrides
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        item = request.data.get("item")
        price = request.data.get("price")
        date_str = request.data.get("date")

        # Basic validation
        if not item or price is None or not date_str:
            return Response(
                {"error": "date, item, and price are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate date
        date_obj = parse_date(str(date_str))
        if not date_obj:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate price is numeric
        try:
            price_val = float(price)
        except (TypeError, ValueError):
            return Response(
                {"error": "price must be a number"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create or update the override for that date
        override, created = MealOverride.objects.update_or_create(
            date=date_obj,
            defaults={"item": item, "price": price_val}
        )

        return Response(
            {
                "message": "Override created" if created else "Override updated",
                "id": override.id,
                "date": str(override.date),
                "item": override.item,
                "price": override.price,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        # Only 'frahman' can delete overrides (by DATE at this endpoint)
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        date_str = request.data.get("date")
        if not date_str:
            return Response({"error": "date is required to clear override"}, status=status.HTTP_400_BAD_REQUEST)

        date_obj = parse_date(str(date_str))
        if not date_obj:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            override = MealOverride.objects.get(date=date_obj)
            override.delete()
            return Response({"message": f"Override for {date_obj} cleared"}, status=status.HTTP_200_OK)
        except MealOverride.DoesNotExist:
            return Response({"error": "No override found for this date"}, status=status.HTTP_404_NOT_FOUND)


# --- Option B: detail GET/PUT/PATCH/DELETE at /api/meal/override/<pk>/ ---

class MealOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealOverride
        fields = ["id", "date", "item", "price", "created_at", "updated_at"]


class OverrideMealDetailView(RetrieveAPIView):
    """
    GET    /api/meal/override/<pk>/
    PUT    /api/meal/override/<pk>/     (full update)
    PATCH  /api/meal/override/<pk>/     (partial update)
    DELETE /api/meal/override/<pk>/     (delete by id)  ← ADDED
    """
    queryset = MealOverride.objects.all()
    serializer_class = MealOverrideSerializer
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        # Only 'frahman' can update overrides
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

        item = request.data.get("item")
        price = request.data.get("price")
        date_str = request.data.get("date")

        # Full update requires all fields
        if not item or price is None or not date_str:
            return Response(
                {"error": "date, item, and price are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        date_obj = parse_date(str(date_str))
        if not date_obj:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            price_val = float(price)
        except (TypeError, ValueError):
            return Response({"error": "price must be a number"}, status=status.HTTP_400_BAD_REQUEST)

        override.date = date_obj
        override.item = item
        override.price = price_val
        try:
            override.save()
        except IntegrityError:
            # Unique date constraint violated
            return Response(
                {"error": "An override for this date already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "message": "Override updated successfully",
                "id": override.id,
                "date": str(override.date),
                "item": override.item,
                "price": override.price,
            },
            status=status.HTTP_200_OK
        )

    def patch(self, request, pk):
        # Only 'frahman' can update overrides
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

        # Update only provided fields
        if "date" in request.data:
            date_obj = parse_date(str(request.data.get("date")))
            if not date_obj:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)
            override.date = date_obj

        if "item" in request.data:
            item = request.data.get("item")
            if not item:
                return Response({"error": "item cannot be empty"}, status=status.HTTP_400_BAD_REQUEST)
            override.item = item

        if "price" in request.data:
            try:
                override.price = float(request.data.get("price"))
            except (TypeError, ValueError):
                return Response({"error": "price must be a number"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            override.save()
        except IntegrityError:
            return Response(
                {"error": "An override for this date already exists."},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response(
            {
                "message": "Override partially updated",
                "id": override.id,
                "date": str(override.date),
                "item": override.item,
                "price": override.price,
            },
            status=status.HTTP_200_OK
        )

    # ✅ ADDED: delete by id
    def delete(self, request, pk):
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

        override.delete()
        return Response({"message": f"Override {pk} deleted"}, status=status.HTTP_200_OK)
