# meal/models.py
from django.db import models

class Meal(models.Model):
    class Weekday(models.TextChoices):
        MONDAY    = "monday", "Monday"
        TUESDAY   = "tuesday", "Tuesday"
        WEDNESDAY = "wednesday", "Wednesday"
        THURSDAY  = "thursday", "Thursday"
        FRIDAY    = "friday", "Friday"
        SATURDAY  = "saturday", "Saturday"
        SUNDAY    = "sunday", "Sunday"

    item = models.CharField(max_length=255)
    price = models.FloatField()

    # New column: only allows Monday–Sunday (stored lowercase)
    day = models.CharField(
        max_length=10,
        choices=Weekday.choices,
        null=True, blank=True,      # ← keep nullable for existing rows; you can make it required later
        db_index=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.item} ({self.price})"
