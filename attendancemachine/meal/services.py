# meal/services.py
from datetime import date as date_cls
from django.db import transaction, connections
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.utils.timezone import localtime, is_naive, make_aware, get_default_timezone
from django.db.models import Q

from .models import Meal, MealOverride, CookRecord
from .lock import is_locked, cutoff_time, DHAKA_TZ  # ensure lock.py provides these

User = get_user_model()


def _weekday_name(d: date_cls) -> str:
    """
    Returns weekday name in lowercase: 'monday'...'sunday'
    """
    return d.strftime("%A").lower()


def resolve_dish(for_date: date_cls):
    """
    Pick the dish for a given date with this priority:
      1) MealOverride(date) -> item, price, source='override', notes
      2) Meal(day=weekday)  -> item, price, source='weekly', notes=None
      3) None if nothing configured
    """
    ovr = MealOverride.objects.filter(date=for_date).first()
    if ovr:
        return {
            "source": "override",
            "item": ovr.item,
            "price": float(ovr.price),
            "notes": (ovr.notes or None),
        }

    weekday = _weekday_name(for_date)
    weekly = Meal.objects.filter(day=weekday).first()
    if weekly:
        return {
            "source": "weekly",
            "item": weekly.item,
            "price": float(weekly.price),
            "notes": None,
        }

    return None


def _to_dhaka(dt):
    """
    Convert a datetime to Asia/Dhaka localized time.
    Assumes naive datetimes are in DEFAULT_TIME_ZONE.
    """
    if dt is None:
        return None
    if is_naive(dt):
        dt = make_aware(dt, get_default_timezone())
    return localtime(dt, DHAKA_TZ)


def _earliest_punch_map(for_date: date_cls):
    """
    Reads the machine logs DB and returns:
        { emp_code (str) : earliest punch (tz-aware Asia/Dhaka) }

    Expects a DB alias 'logs' pointing to a database that has:
        attendance_logs(user_id, timestamp)
    where 'user_id' stores the employee code.
    """
    sql = """
        SELECT user_id, MIN(timestamp) AS first_punch
        FROM attendance_logs
        WHERE DATE(timestamp) = %s
        GROUP BY user_id
    """
    first = {}
    with connections['logs'].cursor() as cursor:
        cursor.execute(sql, [for_date])
        for emp_code, first_punch in cursor.fetchall():
            first[str(emp_code)] = _to_dhaka(first_punch)
    return first


def _leave_map(for_date: date_cls):
    """
    Returns a mapping of emp_code -> leave_type ('full_day'|'1st_half'|'2nd_half')
    for users who have APPROVED leave containing 'for_date' in their JSON 'date' list.
    """
    from leave.models import Leave
    from profiles.models import Profile

    date_str = for_date.isoformat()

    rows = (Leave.objects
            .filter(status='approved', date__contains=[date_str])
            .values('user_id', 'leave_type'))

    user_leave = {r['user_id']: r['leave_type'] for r in rows}
    if not user_leave:
        return {}

    profs = (Profile.objects
             .filter(user_id__in=list(user_leave.keys()))
             .exclude(emp_code__isnull=True)
             .exclude(emp_code='')
             .values('user_id', 'emp_code'))

    out = {}
    for p in profs:
        out[str(p['emp_code'])] = user_leave[p['user_id']]
    return out


def _all_emp_codes():
    """
    Return a set of all known employee codes (strings) from Profile,
    excluding empty and the sentinel '00'.
    """
    from profiles.models import Profile
    codes = (Profile.objects
             .exclude(emp_code__isnull=True)
             .exclude(emp_code='')
             .exclude(emp_code='00')
             .values_list('emp_code', flat=True))
    return set(str(c) for c in codes)


def _person_map(emp_codes):
    """
    Map emp_code -> { user_id, name }
    where name prefers "first_name last_name" then falls back to username.
    """
    q = (User.objects
         .select_related('profile')
         .filter(profile__emp_code__in=list(emp_codes))
         .values('id', 'username', 'first_name', 'last_name', 'profile__emp_code'))

    out = {}
    for row in q:
        name = (row['first_name'] + ' ' + row['last_name']).strip() or row['username']
        out[str(row['profile__emp_code'])] = {"user_id": row['id'], "name": name}
    return out


def _opted_out_codes(for_date: date_cls):
    """
    Returns a set of emp_codes (strings) that are opted out for 'for_date'
    based on MealOptOut (active records).
    Supported scopes:
      - permanent
      - date
      - range (start_date..end_date inclusive)
    """
    from meal.models import MealOptOut
    from profiles.models import Profile

    q = Q(active=True) & (
        Q(scope="permanent") |
        Q(scope="date", date=for_date) |
        Q(scope="range", start_date__lte=for_date, end_date__gte=for_date)
    )
    user_ids = MealOptOut.objects.filter(q).values_list("user_id", flat=True)
    if not user_ids:
        return set()

    emp_codes = (Profile.objects
                 .filter(user_id__in=list(user_ids))
                 .exclude(emp_code__isnull=True)
                 .exclude(emp_code='')
                 .values_list("emp_code", flat=True))
    return set(str(c) for c in emp_codes)


@transaction.atomic
def generate_cook_record(for_date: date_cls, finalized_by=None, force=False):
    """
    Build/update CookRecord for 'for_date' using:
      - dish resolution (override -> weekly)
      - earliest punch per employee (machine logs)
      - approved leave (JSON dates)
      - opt-outs (permanent/date/range)

    Inclusion rules (base):
      - No machine punch & no leave  -> INCLUDE
      - Leave = full_day             -> ABSENT (unless punched <= 08:00)
      - Leave = 1st_half / 2nd_half  -> INCLUDE
      - Punched <= 08:00             -> INCLUDE even if on leave
      - Punched after 08:00 & no leave -> EXCLUDE (protect the cook)

    Opt-out (MealOptOut):
      - If employee is opted out for that day -> EXCLUDE (reason='opted_out'), regardless of base rules.

    Lock:
      - If is_locked(for_date) and not force -> raise ValueError
    """
    if is_locked(for_date) and not force:
        raise ValueError(f"Changes for {for_date} are locked after 08:00 Asia/Dhaka.")

    dish = resolve_dish(for_date)

    # If no dish is configured, create/update a placeholder safely
    if not dish:
        rec, _ = CookRecord.objects.update_or_create(
            date=for_date,
            defaults={
                "source": "manual",
                "item": "UNSET",
                "price": 0.0,
                "notes": None,
                "present_count": 0,
                "on_leave_count": 0,
                "eaters_count": 0,
                "eaters": [],
                "cutoff_time": cutoff_time(),
            },
        )
        if finalized_by is not None:
            rec.is_finalized = True
            rec.finalized_at = timezone.now()
            rec.finalized_by = finalized_by
            rec.save()
        return rec

    # Attendance & leave keyed by emp_code
    first_punch = _earliest_punch_map(for_date)   # dict: emp_code -> earliest local time
    leave_by_code = _leave_map(for_date)          # dict: emp_code -> leave_type
    all_codes = _all_emp_codes() | set(first_punch.keys()) | set(leave_by_code.keys())

    # Opt-outs for this date
    opted_out = _opted_out_codes(for_date)

    CUTOFF = cutoff_time()
    eaters = []
    present_count = sum(1 for _c, t in first_punch.items() if t is not None)
    on_leave_count = len(leave_by_code)
    included_count = 0

    people = _person_map(all_codes)

    for code in sorted(all_codes, key=lambda x: (x is None, x)):
        punch_dt = first_punch.get(code)   # tz-aware or None
        leave_type = leave_by_code.get(code)  # 'full_day' | '1st_half' | '2nd_half' | None
        attended = punch_dt is not None

        # Base inclusion decision
        if attended and punch_dt.time() <= CUTOFF:
            included = True
            reason = "present_before_cutoff"
        elif leave_type in ("1st_half", "2nd_half"):
            included = True
            reason = "half_day_leave_included"
        elif not attended and leave_type is None:
            included = True
            reason = "no_punch_no_leave_included"
        elif attended and leave_type is None and punch_dt.time() > CUTOFF:
            included = False
            reason = "present_after_cutoff"
        elif leave_type == "full_day" and not attended:
            included = False
            reason = "full_day_leave"
        else:
            included = False
            reason = "excluded"

        # Opt-out overrides inclusion
        if code in opted_out:
            included = False
            reason = "opted_out"

        if included:
            included_count += 1

        person = people.get(code)
        eaters.append({
            "emp_code": code,
            "user_id": person["user_id"] if person else None,
            "name": person["name"] if person else f"Emp-{code}",
            "attended": attended,
            "leave_type": leave_type,
            "first_punch_time": punch_dt.isoformat() if punch_dt else None,
            "included": included,
            "inclusion_reason": reason,
        })

    # Save snapshot (insert or update) with required fields to avoid NULLs
    rec, _created = CookRecord.objects.update_or_create(
        date=for_date,
        defaults={
            "source": dish["source"],
            "item": dish["item"],
            "price": float(dish["price"]),
            "notes": dish.get("notes"),
            "present_count": present_count,
            "on_leave_count": on_leave_count,
            "eaters_count": included_count,
            "eaters": eaters,
            "cutoff_time": CUTOFF,
        },
    )

    if finalized_by is not None:
        rec.is_finalized = True
        rec.finalized_at = timezone.now()
        rec.finalized_by = finalized_by
        rec.save()

    return rec
