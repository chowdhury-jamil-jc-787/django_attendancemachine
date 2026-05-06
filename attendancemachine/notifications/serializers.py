# notifications/serializers.py

import hashlib
from rest_framework import serializers
from .models import UserDeviceToken


class UserDeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDeviceToken
        fields = ["id", "token", "platform"]

    def create(self, validated_data):
        user = self.context["request"].user
        token = validated_data["token"]
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        device, created = UserDeviceToken.objects.update_or_create(
            token_hash=token_hash,
            defaults={
                "user": user,
                "token": token,
                "platform": validated_data.get("platform", "android"),
                "is_active": True,
            }
        )

        return device