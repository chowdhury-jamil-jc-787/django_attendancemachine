# leave/serializers.py
from rest_framework import serializers
from datetime import date as date_cls
from .models import Leave

class LeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Leave
        fields = [
            'id', 'leave_type', 'reason',
            'date',                    # JSON list or single string
            'status', 'email_body', 'created_at',
            'informed_status',
        ]
        read_only_fields = ['status', 'email_body', 'created_at']

    def validate(self, data):
        user = self.context['request'].user
        leave_type = data.get('leave_type')
        reason = data.get('reason')
        dates = data.get('date')

        # Normalize dates → list of ISO strings
        if not dates:
            raise serializers.ValidationError({"date": "Provide at least one date."})
        if isinstance(dates, str) or isinstance(dates, date_cls):
            dates = [dates]
        if not isinstance(dates, list) or len(dates) == 0:
            raise serializers.ValidationError({"date": "Must be a list or a single date string."})

        norm = []
        for d in dates:
            if isinstance(d, date_cls):
                d = d.isoformat()
            try:
                date_cls.fromisoformat(d)
            except Exception:
                raise serializers.ValidationError({"date": f"Invalid date: {d}"})
            norm.append(d)

        # Dedup + sort
        norm = sorted(set(norm))

        # 🔒 Half-day must be a single date
        if leave_type in ('1st_half', '2nd_half') and len(norm) != 1:
            raise serializers.ValidationError({"date": "Half-day leave requires exactly one date."})

        # Full-day reason restriction (your rule)
        if leave_type == 'full_day' and reason not in dict(Leave.FULL_DAY_REASONS):
            raise serializers.ValidationError({"reason": "Invalid reason for full-day (personal/family/sick)."})

        # Overlap check with pending/approved leaves (any shared date)
        existing = Leave.objects.filter(
            user=user,
            status__in=['approved', 'pending']
        ).exclude(id=self.instance.id if self.instance else None)

        for other in existing:
            if any(d in (other.date or []) for d in norm):
                # (Optional: allow combining 1st_half + 2nd_half same day — uncomment next block to allow)
                # if (leave_type in ('1st_half', '2nd_half') and len(norm) == 1 and
                #     other.date and len(other.date) == 1 and
                #     norm[0] == other.date[0] and
                #     {leave_type, other.leave_type} == {'1st_half', '2nd_half'}):
                #     continue
                raise serializers.ValidationError({"date": "Overlaps with an existing leave."})

        data['date'] = norm
        return data


class LeaveListSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    leave_type_display = serializers.SerializerMethodField()

    class Meta:
        model = Leave
        fields = [
            'id', 'leave_type', 'leave_type_display', 'reason',
            'date', 'status', 'is_approved', 'informed_status',
            'email_body', 'created_at', 'updated_at', 'user'
        ]

    def get_user(self, obj):
        u = obj.user
        return {
            "id": u.id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
        }

    def get_leave_type_display(self, obj):
        return obj.get_leave_type_display()
    


