# meal/locks.py
from datetime import time
from django.utils import timezone
import pytz

DHAKA_TZ = pytz.timezone("Asia/Dhaka")
CUTOFF = time(8, 0, 0)  # 08:00:00

def is_locked(target_date):
    """
    Locked if:
      - date < today (Dhaka), or
      - date == today and local time >= 08:00
    """
    now_local = timezone.localtime(timezone.now(), DHAKA_TZ)
    today_local = now_local.date()
    if target_date < today_local:
        return True
    if target_date == today_local and now_local.time() >= CUTOFF:
        return True
    return False

def cutoff_time():
    return CUTOFF