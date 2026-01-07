from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Member, MemberAssignment

User = get_user_model()


class MemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = Member
        fields = ['id', 'name', 'email', 'position']
        extra_kwargs = {
            'position': {'required': False, 'allow_blank': True}
        }


class MemberAssignmentSerializer(serializers.ModelSerializer):
    # user is REQUIRED
    user_id = serializers.PrimaryKeyRelatedField(
        source='user',
        queryset=User.objects.all()
    )

    # member is OPTIONAL (nullable)
    member_id = serializers.PrimaryKeyRelatedField(
        source='member',
        queryset=Member.objects.all(),
        required=False,
        allow_null=True
    )

    # sign_in is OPTIONAL (nullable)
    sign_in_id = serializers.PrimaryKeyRelatedField(
        source='sign_in',
        queryset=Member.objects.all(),
        required=False,
        allow_null=True
    )

    class Meta:
        model = MemberAssignment
        fields = [
            'id',
            'user_id',
            'member_id',
            'sign_in_id',
            'created_at'
        ]
