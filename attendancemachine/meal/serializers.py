# meal/serializers.py
from rest_framework import serializers
from .models import Meal, CookRecord, MealOptOut
from .lock import is_locked
from django.utils import timezone


class MealSerializer(serializers.ModelSerializer):
    class Meta:
        model = Meal
        fields = ["id", "item", "price", "day", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_day(self, value):
        if value in (None, ""):
            return value  # allow null/blank if your model allows it
        v = str(value).strip().lower()
        allowed = {choice[0] for choice in Meal.Weekday.choices}
        if v not in allowed:
            raise serializers.ValidationError(f"Day must be one of: {sorted(allowed)}")
        return v
    

class CookRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = CookRecord
        fields = [
            "id", "date", "source", "item", "price", "notes",
            "present_count", "on_leave_count", "eaters_count",
            "eaters", "cutoff_time",
            "is_finalized", "finalized_at", "finalized_by",
            "created_at", "updated_at",
        ]
        read_only_fields = ["finalized_by", "created_at", "updated_at"]


class MealOptOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = MealOptOut
        fields = [
            "id","user","scope","date","start_date","end_date",
            "reason","active","created_by","created_at","updated_at"
        ]
        read_only_fields = ["created_by","created_at","updated_at"]

    def validate(self, attrs):
        scope = attrs.get("scope", getattr(self.instance, "scope", None))
        date = attrs.get("date", getattr(self.instance, "date", None))
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))

        if scope == "permanent":
            if date or start_date or end_date:
                raise serializers.ValidationError("Permanent opt-out must not include date/start_date/end_date.")
        elif scope == "date":
            if not date:
                raise serializers.ValidationError("date is required for scope='date'.")
            # Do not allow creating/updating an opt-out that targets a locked date
            if is_locked(date):
                raise serializers.ValidationError(f"{date} is locked (after 08:00 or in the past).")
        elif scope == "range":
            if not start_date or not end_date:
                raise serializers.ValidationError("start_date and end_date are required for scope='range'.")
            if start_date > end_date:
                raise serializers.ValidationError("start_date cannot be after end_date.")
            # If today is locked and today falls in range, block
            today = timezone.localdate()
            if is_locked(today) and (start_date <= today <= end_date):
                raise serializers.ValidationError("Today is locked; range affecting today cannot be created/updated now.")
        else:
            raise serializers.ValidationError("Invalid scope.")

        return attrs




