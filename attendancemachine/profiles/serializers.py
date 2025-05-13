from rest_framework import serializers
from .models import Profile
from datetime import datetime
from django.utils.text import slugify

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['profile_img', 'birthday', 'phone_number']
        extra_kwargs = {
            'profile_img': {'required': False, 'allow_null': True},
            'birthday': {'required': False, 'allow_null': True},
            'phone_number': {'required': False, 'allow_null': True},
        }

    def validate_profile_img(self, image):
        if not image:
            return None  # support clearing the image with null

        user = self.context.get('request').user
        username = slugify(user.username)
        now = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # Rename the uploaded file
        ext = image.name.split('.')[-1]
        image.name = f"{now}_{username}.{ext}"

        # Validate file type and size
        max_size = 2 * 1024 * 1024  # 2 MB
        valid_extensions = ['jpg', 'jpeg', 'png']

        if image.size > max_size:
            raise serializers.ValidationError("Image size must be under 2MB.")
        if ext.lower() not in valid_extensions:
            raise serializers.ValidationError("Invalid file type. Only JPG, JPEG, PNG allowed.")

        return image