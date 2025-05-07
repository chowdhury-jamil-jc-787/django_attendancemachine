from rest_framework import serializers
from .models import Profile
from datetime import datetime
from django.utils.text import slugify

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['profile_img', 'birthday', 'phone_number']

    def validate_profile_img(self, image):
        # Extract current user from serializer context
        user = self.context['request'].user
        username = slugify(user.username)
        now = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # Rename uploaded image
        ext = image.name.split('.')[-1]
        image.name = f"{now}_{username}.{ext}"

        # Optional: Validate file type and size
        max_size = 2 * 1024 * 1024  # 2 MB
        valid_extensions = ['jpg', 'jpeg', 'png']
        if image.size > max_size:
            raise serializers.ValidationError("Image size must be under 2MB.")
        if ext.lower() not in valid_extensions:
            raise serializers.ValidationError("Invalid file type. Only JPG, JPEG, PNG allowed.")

        return image