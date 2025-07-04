from rest_framework import serializers
from django.utils.timezone import now
from django.db.models import Q
from .models import Leave
import datetime

class LeaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Leave
        fields = [
            'id', 'leave_type', 'reason',
            'date', 'start_date', 'end_date',
            'status', 'email_body', 'created_at'
        ]
        read_only_fields = ['status', 'email_body', 'created_at']

    def validate(self, data):
        user = self.context['request'].user
        leave_type = data.get('leave_type')
        reason = data.get('reason')

        # Reason restriction for full_day
        if leave_type == 'full_day' and reason not in ['personal', 'family', 'sick']:
            raise serializers.ValidationError("Invalid reason for full-day leave.")

        # Determine all dates to check
        if leave_type == 'half_day':
            leave_dates = [data.get('date')]
            if not leave_dates[0]:
                raise serializers.ValidationError("Half-day leave must have a valid date.")
        else:
            start = data.get('start_date') or data.get('date')
            end = data.get('end_date') or data.get('date')
            if not start or not end:
                raise serializers.ValidationError("Full-day leave must have start and end date.")
            if start > end:
                raise serializers.ValidationError("Start date cannot be after end date.")

            leave_dates = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]

        # Check for overlapping existing leave (pending or approved)
        existing = Leave.objects.filter(
            user=user,
            status__in=['approved', 'pending']
        ).exclude(id=self.instance.id if self.instance else None)

        for date in leave_dates:
            if existing.filter(
                Q(date=date) |
                Q(start_date__lte=date, end_date__gte=date)
            ).exists():
                raise serializers.ValidationError(
                    f"You already have a leave request on {date}."
                )

        return data
