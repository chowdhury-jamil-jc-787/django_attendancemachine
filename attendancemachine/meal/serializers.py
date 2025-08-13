# meal/serializers.py
from rest_framework import serializers
from .models import Meal

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
