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
    # ======================
    # WRITE FIELDS (IDs)
    # ======================

    user_id = serializers.PrimaryKeyRelatedField(
        source='user',
        queryset=User.objects.all()
    )

    member_id = serializers.PrimaryKeyRelatedField(
        source='member',
        queryset=Member.objects.all(),
        required=False,
        allow_null=True
    )

    sign_in_id = serializers.PrimaryKeyRelatedField(
        source='sign_in',
        queryset=Member.objects.all(),
        required=False,
        allow_null=True
    )

    # ======================
    # READ-ONLY FIELDS (NAMES)
    # ======================

    member_name = serializers.SerializerMethodField(read_only=True)
    sign_in_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = MemberAssignment
        fields = [
            'id',

            'user_id',

            'member_id',
            'member_name',

            'sign_in_id',
            'sign_in_name',

            'created_at',
        ]

    # ======================
    # METHODS
    # ======================

    def get_member_name(self, obj):
        if obj.member:
            return obj.member.name
        return None

    def get_sign_in_name(self, obj):
        if obj.sign_in:
            return obj.sign_in.name
        return None
