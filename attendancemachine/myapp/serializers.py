
from rest_framework import serializers
from django.contrib.auth.models import User


class UserSerializer(serializers.ModelSerializer):
    emp_code = serializers.CharField(
        source='profile.emp_code',
        read_only=True
    )

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'date_joined',
            'emp_code'
        ]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True
    )

    emp_code = serializers.CharField(
        write_only=True
    )

    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'password',
            'first_name',
            'last_name',
            'emp_code'
        ]

    def create(self, validated_data):
        emp_code = validated_data.pop('emp_code')

        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data.get('email'),
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', '')
        )

        # Save employee code into the associated profile.
        user.profile.emp_code = emp_code
        user.profile.save()

        return user


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(
        required=True
    )

    password = serializers.CharField(
        required=True,
        write_only=True
    )


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(
        required=True,
        write_only=True
    )

    new_password = serializers.CharField(
        required=True,
        write_only=True
    )

    confirm_password = serializers.CharField(
        required=True,
        write_only=True
    )

    # New: Logout all devices after password change.
    logout_all_devices = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True
    )

    # New: Logout other devices but keep current device.
    logout_other_devices = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True
    )

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError(
                "New password and confirm password do not match."
            )

        return data
