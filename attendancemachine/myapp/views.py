from django.shortcuts import render
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated 
from rest_framework_simplejwt.tokens import RefreshToken, TokenError, AccessToken
from rest_framework.permissions import AllowAny
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from .serializers import RegisterSerializer, LoginSerializer, UserSerializer, ChangePasswordSerializer
from rest_framework import status, permissions
from rest_framework_simplejwt.authentication import JWTAuthentication
from .models import BlacklistedAccessToken
from datetime import datetime
from rest_framework_simplejwt.tokens import AccessToken
import jwt
from rest_framework.permissions import AllowAny

# Create your views here.
class RegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]  # ✅ this makes it public
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        refresh = RefreshToken.for_user(user)
        user_serializer = UserSerializer(user)

        return Response({
            'status': True,  # ✅ Custom status message
            'id': user.id,
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': user_serializer.data
        }, status=status.HTTP_201_CREATED)

class LoginView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        username = request.data.get('username')
        password = request.data.get('password')
        user = authenticate(username=username, password=password)

        if user is not None:
            refresh = RefreshToken.for_user(user)
            user_serializer = UserSerializer(user)
            return Response({
                "status": True,  # ✅ Custom status message
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": user_serializer.data
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                "status": "Login failed",
                "error": "Invalid credentials"
            }, status=status.HTTP_401_UNAUTHORIZED)
       
class DashboardView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        # Step 1: Extract raw token from Authorization header
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({"error": "Authorization header missing or malformed"}, status=401)

        raw_token = auth_header.split(' ')[1]

        # Step 2: Decode raw token (without verifying signature)
        try:
            decoded = jwt.decode(raw_token, options={"verify_signature": False})
            token_jti = decoded.get("jti")
        except Exception:
            return Response({"error": "Invalid token format"}, status=401)

        # Step 3: Check if JTI exists in blacklist
        if token_jti and BlacklistedAccessToken.objects.filter(jti=token_jti).exists():
            return Response({"error": "Your token has been blacklisted."}, status=401)

        # Step 4: Proceed if token is valid
        user = request.user
        user_serializer = UserSerializer(user)
        return Response({
            "success": True,
            "user": user_serializer.data
        }, status=200)
    
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Bearer '):
            return Response({"error": "Invalid authorization header"}, status=400)

        raw_token = auth_header.split(" ")[1]

        # Decode without verifying signature
        try:
            decoded = jwt.decode(raw_token, options={"verify_signature": False})
            token_type = decoded.get("token_type")
        except Exception as e:
            return Response({"error": f"Invalid token format: {str(e)}"}, status=400)

        if token_type == "access":
            try:
                token = AccessToken(raw_token)
                BlacklistedAccessToken.objects.get_or_create(
                    jti=token['jti'],
                    defaults={'expires_at': datetime.fromtimestamp(token['exp'])}
                )
                return Response({"message": "Access token blacklisted"}, status=205)
            except Exception:
                return Response({"error": "Invalid access token"}, status=400)

        elif token_type == "refresh":
            try:
                token = RefreshToken(raw_token)
                token.blacklist()
                return Response({"message": "Refresh token blacklisted"}, status=205)
            except TokenError as e:
                return Response({"error": str(e)}, status=400)

        return Response({"error": "Unsupported token type"}, status=400)
    

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        old_password = serializer.validated_data['old_password']
        new_password = serializer.validated_data['new_password']

        if not user.check_password(old_password):
            return Response({"error": "Old password is incorrect."}, status=400)

        user.set_password(new_password)
        user.save()

        return Response({"message": "Password updated successfully."}, status=200)