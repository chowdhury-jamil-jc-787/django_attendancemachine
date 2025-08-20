# meal/admin.py (append)
from django.contrib import admin
from .models import MealOptOut
@admin.register(MealOptOut)
class MealOptOutAdmin(admin.ModelAdmin):
    list_display = ("id","user","scope","date","start_date","end_date","active","created_at")
    list_filter = ("scope","active")
    search_fields = ("user__username","user__email")
