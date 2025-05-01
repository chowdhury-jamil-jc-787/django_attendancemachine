from django.shortcuts import render
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import RegisterSerializer, UserSerializer
from .models import Profile
from django.contrib.auth.models import User

# Create your views here.
class RegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Optional: Extract profile fields if provided
        profile_data = {
            'birthday': request.data.get('birthday'),
            'phone_number': request.data.get('phone_number'),
            'profile_img': request.FILES.get('profile_img')  # requires multipart/form-data
        }
        Profile.objects.create(user=user, **{k: v for k, v in profile_data.items() if v is not None})

        refresh = RefreshToken.for_user(user)
        user_serializer = UserSerializer(user)

        return Response({
            'status': True,
            'id': user.id,
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': user_serializer.data
        }, status=status.HTTP_201_CREATED)