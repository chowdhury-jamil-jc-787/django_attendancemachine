# meal/models.py
from django.db import models


class Meal(models.Model):
    """
    Stores the default weekly menu.
    Example: Monday → Chicken Curry, Tuesday → Fish Fry, etc.
    """

    class Weekday(models.TextChoices):
        MONDAY = "monday", "Monday"
        TUESDAY = "tuesday", "Tuesday"
        WEDNESDAY = "wednesday", "Wednesday"
        THURSDAY = "thursday", "Thursday"
        FRIDAY = "friday", "Friday"
        SATURDAY = "saturday", "Saturday"
        SUNDAY = "sunday", "Sunday"

    day = models.CharField(
        max_length=10,
        choices=Weekday.choices,
        null=True, blank=True,   # ✅ allow NULL for now
        db_index=True
    )
    item = models.CharField(max_length=255)
    price = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.day.capitalize()} - {self.item} ({self.price})"


class MealOverride(models.Model):
    """
    Stores exceptions for specific dates when admin changes the menu.
    Example: On 2025-08-21 (Thursday), instead of Fish Fry → Special Biriyani.
    """

    date = models.DateField(unique=True, db_index=True)  # Each date can have only one override
    item = models.CharField(max_length=255)
    price = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.date} - {self.item} ({self.price})"

