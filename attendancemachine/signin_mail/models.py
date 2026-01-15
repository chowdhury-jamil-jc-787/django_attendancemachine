from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()

class DailySignInMailLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("user", "date")
