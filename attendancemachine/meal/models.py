# meal/models.py
from django.db import models
from django.conf import settings

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
    notes = models.TextField(blank=True, null=True)  # ✅ NEW: reason or explanation for override

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.date} - {self.item} ({self.price})"



class CookRecord(models.Model):
    SOURCE_CHOICES = [
        ("weekly", "Weekly"),
        ("override", "Override"),
        ("manual", "Manual"),
    ]

    date = models.DateField(unique=True, db_index=True)

    # Final chosen dish for that date (snapshot)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    item = models.CharField(max_length=255)
    price = models.FloatField()
    notes = models.TextField(blank=True, null=True)

    # Snapshot counts
    present_count = models.IntegerField(default=0)
    on_leave_count = models.IntegerField(default=0)
    eaters_count = models.IntegerField(default=0)

    # Who ate (snapshot) — JSON list of dicts
    # Each entry: {user_id, name, attended, on_leave, first_punch_time, included, inclusion_reason}
    eaters = models.JSONField(default=list, blank=True)

    # Locking & audit
    cutoff_time = models.TimeField(default=None, null=True, blank=True)  # store 08:00 used
    is_finalized = models.BooleanField(default=False)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="finalized_cook_records"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.date} → {self.item} ({self.eaters_count})"
    


class MealOptOut(models.Model):
    SCOPE_CHOICES = [
        ("permanent", "Permanent"),
        ("date", "Single Date"),
        ("range", "Date Range"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meal_optouts")
    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES)

    # For scope='date'
    date = models.DateField(null=True, blank=True, db_index=True)

    # For scope='range'
    start_date = models.DateField(null=True, blank=True, db_index=True)
    end_date   = models.DateField(null=True, blank=True, db_index=True)

    reason = models.TextField(blank=True, null=True)
    active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_meal_optouts"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["scope", "date"]),
            models.Index(fields=["scope", "start_date", "end_date"]),
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        base = f"{self.user_id} {self.scope}"
        if self.scope == "date":
            base += f" @ {self.date}"
        elif self.scope == "range":
            base += f" {self.start_date}→{self.end_date}"
        return base
