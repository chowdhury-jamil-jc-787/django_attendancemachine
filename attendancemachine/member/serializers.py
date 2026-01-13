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
    # WRITE FIELDS (OPTIONAL)
    # ======================

    user_id = serializers.PrimaryKeyRelatedField(
        source='user',
        queryset=User.objects.all(),
        required=False,
        allow_null=True
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
    # READ-ONLY FIELDS
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
    # VALIDATION (CRITICAL)
    # ======================

    def validate(self, attrs):
        member = attrs.get('member')
        sign_in = attrs.get('sign_in')

        # ❌ both provided
        if member and sign_in:
            raise serializers.ValidationError(
                "Provide either member_id or sign_in_id, not both."
            )

        # ❌ none provided (on create)
        if not member and not sign_in and self.instance is None:
            raise serializers.ValidationError(
                "Either member_id or sign_in_id is required."
            )

        return attrs

    # ======================
    # READ HELPERS
    # ======================

    def get_member_name(self, obj):
        return obj.member.name if obj.member else None

    def get_sign_in_name(self, obj):
        return obj.sign_in.name if obj.sign_in else None
