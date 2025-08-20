# meal/views.py
from django.utils.dateparse import parse_date
from django.db import IntegrityError
from rest_framework import viewsets, permissions, status, serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import RetrieveAPIView

from .models import Meal, MealOverride
from .serializers import MealSerializer, CookRecordSerializer, MealOptOutSerializer

from datetime import date as date_cls
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from .models import CookRecord, MealOptOut
from .services import generate_cook_record
from .lock import is_locked, cutoff_time, DHAKA_TZ
from django.shortcuts import get_object_or_404
from django.db import models


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
        body: { "date": "YYYY-MM-DD", "item": "...", "price": 123.45, "notes": "..." }
    - DELETE /api/meal/override/            -> delete by date
        body: { "date": "YYYY-MM-DD" }
    """
    permission_classes = [permissions.IsAuthenticated]

    # ✅ list all overrides
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
        notes = request.data.get("notes")

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
            defaults={"item": item, "price": price_val, "notes": notes}
        )

        return Response(
            {
                "message": "Override created" if created else "Override updated",
                "id": override.id,
                "date": str(override.date),
                "item": override.item,
                "price": override.price,
                "notes": override.notes,
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


# --- detail GET/PUT/PATCH/DELETE at /api/meal/override/<pk>/ ---

class MealOverrideSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealOverride
        fields = ["id", "date", "item", "price", "notes", "created_at", "updated_at"]


class OverrideMealDetailView(RetrieveAPIView):
    """
    GET    /api/meal/override/<pk>/
    PUT    /api/meal/override/<pk>/     (full update)
    PATCH  /api/meal/override/<pk>/     (partial update)
    DELETE /api/meal/override/<pk>/     (delete by id)
    """
    queryset = MealOverride.objects.all()
    serializer_class = MealOverrideSerializer
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

        item = request.data.get("item")
        price = request.data.get("price")
        date_str = request.data.get("date")
        notes = request.data.get("notes")

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
        override.notes = notes
        try:
            override.save()
        except IntegrityError:
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
                "notes": override.notes,
            },
            status=status.HTTP_200_OK
        )

    def patch(self, request, pk):
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

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

        if "notes" in request.data:
            override.notes = request.data.get("notes")

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
                "notes": override.notes,
            },
            status=status.HTTP_200_OK
        )

    def delete(self, request, pk):
        if request.user.username != "frahman":
            return Response({"error": "Permission denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            override = MealOverride.objects.get(pk=pk)
        except MealOverride.DoesNotExist:
            return Response({"error": "Override not found"}, status=status.HTTP_404_NOT_FOUND)

        override.delete()
        return Response({"message": f"Override {pk} deleted"}, status=status.HTTP_200_OK)




class CookRecordGenerateView(APIView):
    """
    POST /api/cook-records/generate/?date=YYYY-MM-DD
    - Before 08:00: generates/updates snapshot for that date
    - At/After 08:00 for today: blocked (locked)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        date_str = request.query_params.get("date")
        d = parse_date(date_str) if date_str else timezone.localdate()
        try:
            rec = generate_cook_record(d, finalized_by=None, force=False)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_423_LOCKED)

        return Response(CookRecordSerializer(rec).data, status=status.HTTP_200_OK)

class CookRecordDetailView(APIView):
    """
    GET  /api/cook-records/?date=YYYY-MM-DD
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "date query param is required"}, status=400)
        d = parse_date(date_str)
        if not d:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=400)

        rec = CookRecord.objects.filter(date=d).first()
        if not rec:
            # 👇 auto-generate and save, then show
            rec = generate_cook_record(d, finalized_by=None, force=True)

        return Response(CookRecordSerializer(rec).data, status=200)

class CookRecordFinalizeTodayView(APIView):
    """
    POST /api/cook-records/finalize-today/
    - Run at/after 08:00 Asia/Dhaka to lock today's CookRecord.
    - Use ?force=1 to allow finalizing earlier (e.g., admin/script), or to re-run safely.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        d = timezone.localdate()
        force_param = str(request.query_params.get("force", "")).lower()
        force = force_param in ("1", "true", "t", "yes", "y")

        # If it's already finalized, return early with the record
        rec = CookRecord.objects.filter(date=d).first()
        if rec and rec.is_finalized:
            return Response(
                {"message": "Already finalized", "record": CookRecordSerializer(rec).data},
                status=status.HTTP_200_OK
            )

        # Block manual finalization before cutoff unless forced
        now_local = timezone.localtime(timezone.now(), DHAKA_TZ)
        cutoff = cutoff_time()
        if not force and now_local.time() < cutoff:
            return Response(
                {
                    "error": "Too early to finalize. Finalization opens at 08:00 Asia/Dhaka.",
                    "now_local": now_local.strftime("%H:%M:%S"),
                    "cutoff": cutoff.strftime("%H:%M:%S"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate (or re-generate) snapshot and finalize it
        # force=True ensures generation runs even if time passed or lock logic would block writes
        rec = generate_cook_record(d, finalized_by=request.user, force=True)
        rec.is_finalized = True
        rec.finalized_at = timezone.now()
        rec.finalized_by = request.user
        rec.save()

        return Response(
            {"message": "Finalized", "record": CookRecordSerializer(rec).data},
            status=status.HTTP_200_OK
        )
    






def _is_admin(user):
    return user.username == "frahman"

class MealOptOutListCreateView(APIView):
    """
    GET  /api/meal/opt-outs/?user_id=&active=&scope=&date=
    POST /api/meal/opt-outs/
      - Non-admin: user is forced to request.user
      - Admin: can set any user
      - Lock rule: cannot create opt-out that targets a locked date (for scope='date' or range including today)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = MealOptOut.objects.select_related("user").all()

        uid = request.query_params.get("user_id")
        active = request.query_params.get("active")
        scope = request.query_params.get("scope")
        date_str = request.query_params.get("date")

        # access control
        if not _is_admin(request.user):
            qs = qs.filter(user=request.user)
        elif uid:
            qs = qs.filter(user_id=uid)

        if active is not None:
            val = str(active).lower()
            if val in ("1","true","t","yes","y"):
                qs = qs.filter(active=True)
            elif val in ("0","false","f","no","n"):
                qs = qs.filter(active=False)

        if scope:
            qs = qs.filter(scope=scope)

        # filter by "effective on date"
        if date_str:
            d = parse_date(date_str)
            if not d:
                return Response({"error":"Invalid date format. Use YYYY-MM-DD."}, status=400)
            qs = qs.filter(
                active=True
            ).filter(
                models.Q(scope="permanent") |
                models.Q(scope="date", date=d) |
                models.Q(scope="range", start_date__lte=d, end_date__gte=d)
            )

        ser = MealOptOutSerializer(qs.order_by("-created_at"), many=True)
        return Response(ser.data, status=200)

    def post(self, request):
        data = request.data.copy()

        # Non-admin: force user field to self
        if not _is_admin(request.user):
            data["user"] = request.user.id

        ser = MealOptOutSerializer(data=data)
        if not ser.is_valid():
            return Response(ser.errors, status=400)

        # Prevent creating/affecting today if locked & scope touches today
        scope = ser.validated_data["scope"]
        today = timezone.localdate()

        if scope == "date":
            if is_locked(ser.validated_data["date"]):
                return Response({"error":"The selected date is locked."}, status=400)
        elif scope == "range":
            s = ser.validated_data["start_date"]
            e = ser.validated_data["end_date"]
            if is_locked(today) and s <= today <= e:
                return Response({"error":"Today is locked; range affecting today cannot be created now."}, status=400)

        obj = ser.save(created_by=request.user)
        return Response(MealOptOutSerializer(obj).data, status=201)


class MealOptOutDetailView(APIView):
    """
    GET    /api/meal/opt-outs/<id>/
    PATCH  /api/meal/opt-outs/<id>/
      - Non-admin: can only update own record
      - Lock rule: cannot change record to affect a locked date
    DELETE /api/meal/opt-outs/<id>/  (admin hard delete; non-admin => soft deactivate)
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, request, pk):
        obj = get_object_or_404(MealOptOut, pk=pk)
        if not _is_admin(request.user) and obj.user_id != request.user.id:
            return None
        return obj

    def get(self, request, pk):
        obj = self.get_object(request, pk)
        if obj is None:
            return Response({"error":"Not allowed"}, status=403)
        return Response(MealOptOutSerializer(obj).data, status=200)

    def patch(self, request, pk):
        obj = self.get_object(request, pk)
        if obj is None:
            return Response({"error":"Not allowed"}, status=403)

        data = request.data.copy()
        # Non-admin cannot change 'user'
        if not _is_admin(request.user):
            data.pop("user", None)

        # Validate lock impact
        scope = data.get("scope", obj.scope)
        today = timezone.localdate()

        # We’ll run serializer validation, but add a hard check here:
        if scope == "date":
            d = parse_date(data.get("date")) if ("date" in data) else obj.date
            if d and is_locked(d):
                return Response({"error":"That date is locked; cannot modify this opt-out."}, status=400)
        elif scope == "range":
            s = parse_date(data.get("start_date")) if ("start_date" in data) else obj.start_date
            e = parse_date(data.get("end_date"))   if ("end_date" in data)   else obj.end_date
            if s and e and is_locked(today) and s <= today <= e:
                return Response({"error":"Today is locked; cannot change a range that affects today."}, status=400)

        ser = MealOptOutSerializer(obj, data=data, partial=True)
        if not ser.is_valid():
            return Response(ser.errors, status=400)
        ser.save()
        return Response(ser.data, status=200)

    def delete(self, request, pk):
        obj = self.get_object(request, pk)
        if obj is None:
            return Response({"error":"Not allowed"}, status=403)

        if _is_admin(request.user):
            obj.delete()
            return Response({"message":"Deleted"}, status=200)
        else:
            # soft deactivate for non-admins
            obj.active = False
            obj.save(update_fields=["active","updated_at"])
            return Response({"message":"Deactivated"}, status=200)