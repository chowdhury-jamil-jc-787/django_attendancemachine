import uuid
from datetime import datetime, timezone as datetime_timezone

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenObtainSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from profiles.serializers import ProfileSerializer

from .models import BlacklistedAccessToken, DeviceSession
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    RegisterSerializer,
    UserSerializer,
)


def _device_name(user_agent):
    """Build a readable device label without an external dependency."""
    agent = (user_agent or "").lower()
    if "iphone" in agent:
        os_name = "iPhone"
    elif "ipad" in agent:
        os_name = "iPad"
    elif "android" in agent:
        os_name = "Android"
    elif "windows" in agent:
        os_name = "Windows"
    elif "mac os" in agent or "macintosh" in agent:
        os_name = "Mac"
    elif "linux" in agent:
        os_name = "Linux"
    else:
        os_name = "Unknown device"

    if "edg/" in agent or "edgios/" in agent or "edga/" in agent:
        browser = "Edge"
    elif "firefox/" in agent or "fxios/" in agent:
        browser = "Firefox"
    elif "chrome/" in agent or "crios/" in agent:
        browser = "Chrome"
    elif "safari/" in agent:
        browser = "Safari"
    else:
        browser = "Browser"
    return f"{browser} on {os_name}"


def _issue_device_tokens(user, request):
    """Create one revocable session and JWT pair containing the same sid."""
    session_id = uuid.uuid4()
    refresh = RefreshToken.for_user(user)
    refresh["sid"] = str(session_id)

    user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]
    # REMOTE_ADDR is used deliberately. X-Forwarded-For is untrusted unless
    # the deployment explicitly configures a trusted reverse proxy.
    ip_address = request.META.get("REMOTE_ADDR") or None
    expires_at = datetime.fromtimestamp(
        int(refresh["exp"]), tz=datetime_timezone.utc
    )

    DeviceSession.objects.create(
        id=session_id,
        user=user,
        device_name=_device_name(user_agent),
        user_agent=user_agent,
        ip_address=ip_address,
        expires_at=expires_at,
    )
    # SimpleJWT copies custom refresh claims (such as sid) into access tokens.
    return str(refresh), str(refresh.access_token)


def _profile_data(user):
    try:
        return ProfileSerializer(user.profile).data
    except Exception:
        # Preserve the previous API behavior when a profile is absent.
        return None


def _current_session_id(request):
    """The JWT has already been validated by CustomJWTAuthentication."""
    return uuid.UUID(str(request.auth["sid"]))


def _revoke_sessions(queryset):
    """Make logout idempotent: only active records are changed."""
    return queryset.filter(revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def _boolean_option(request, key):
    """Accept real booleans and common HTML form boolean values."""
    value = request.data.get(key, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("true", "1", "yes", "on"):
            return True
        if value in ("false", "0", "no", "off", ""):
            return False
    raise ValidationError({key: ["Must be a boolean value."]})


class RegisterView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            user = serializer.save()
            refresh, access = _issue_device_tokens(user, request)

        return Response({
            "status": True,
            "id": user.id,
            "refresh": refresh,
            "access": access,
            "user": UserSerializer(user).data,
            "profile": _profile_data(user),
        }, status=status.HTTP_201_CREATED)


class LoginView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        username = request.data.get("username")
        password = request.data.get("password")
        user = authenticate(
            request=request, username=username, password=password
        )
        if user is None:
            return Response({
                "status": "Login failed",
                "error": "Invalid credentials",
            }, status=status.HTTP_401_UNAUTHORIZED)

        refresh, access = _issue_device_tokens(user, request)
        return Response({
            "status": True,
            "refresh": refresh,
            "access": access,
            "user": UserSerializer(user).data,
            "profile": _profile_data(user),
        }, status=status.HTTP_200_OK)


class DeviceTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Make /api/token/ logins produce revocable device sessions too."""

    def validate(self, attrs):
        data = TokenObtainSerializer.validate(self, attrs)
        refresh, access = _issue_device_tokens(
            self.user, self.context["request"]
        )
        data["refresh"] = refresh
        data["access"] = access
        return data


class DeviceTokenObtainPairView(TokenObtainPairView):
    serializer_class = DeviceTokenObtainPairSerializer


class DeviceTokenRefreshView(TokenRefreshView):
    """Block refresh-token reuse after the corresponding device is logged out."""

    def post(self, request, *args, **kwargs):
        raw_refresh = request.data.get("refresh")
        if not isinstance(raw_refresh, str) or not raw_refresh:
            raise ValidationError({"refresh": ["This field is required."]})

        try:
            refresh = RefreshToken(raw_refresh)
            session_id = uuid.UUID(str(refresh.get("sid")))
            user_id = refresh[api_settings.USER_ID_CLAIM]
        except (TokenError, ValueError, TypeError, KeyError) as exc:
            raise InvalidToken("Invalid or expired device refresh token.") from exc

        session = DeviceSession.objects.filter(
            id=session_id,
            user_id=user_id,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).first()
        if session is None:
            raise InvalidToken(
                "This device is logged out or its session has expired."
            )

        # Preserve SimpleJWT's original refresh-rotation and blacklist logic.
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            updates = {}
            new_refresh = response.data.get("refresh")
            if new_refresh:
                rotated = RefreshToken(new_refresh)
                updates["expires_at"] = datetime.fromtimestamp(
                    int(rotated["exp"]), tz=datetime_timezone.utc
                )
            if updates:
                DeviceSession.objects.filter(
                    id=session_id, revoked_at__isnull=True
                ).update(**updates)
        return response


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # CustomJWTAuthentication already validates the signed access token,
        # access-token blacklist, and active device session.
        return Response({
            "success": True,
            "user": UserSerializer(request.user).data,
            "profile": _profile_data(request.user),
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _revoke_sessions(DeviceSession.objects.filter(
            id=_current_session_id(request), user=request.user
        ))

        # Keep the project's original access-token blacklist mechanism.
        jti = request.auth.get("jti")
        exp = request.auth.get("exp")
        if jti and exp:
            BlacklistedAccessToken.objects.get_or_create(
                jti=jti,
                defaults={
                    "expires_at": datetime.fromtimestamp(
                        int(exp), tz=datetime_timezone.utc
                    )
                },
            )
        return Response(
            {"message": "Access token blacklisted"},
            status=status.HTTP_205_RESET_CONTENT,
        )


class DeviceSessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        current_id = _current_session_id(request)
        sessions = DeviceSession.objects.filter(
            user=request.user,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).order_by("-last_seen_at", "-created_at")

        rows = [{
            "id": str(session.id),
            "device_name": session.device_name,
            "ip_address": session.ip_address,
            "created_at": session.created_at,
            "last_seen_at": session.last_seen_at,
            "expires_at": session.expires_at,
            "is_current": session.id == current_id,
        } for session in sessions]
        return Response({"count": len(rows), "sessions": rows})


class LogoutDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, session_id):
        session = DeviceSession.objects.filter(
            id=session_id,
            user=request.user,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).first()
        if session is None:
            return Response(
                {"error": "Active device session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        current = session.id == _current_session_id(request)
        _revoke_sessions(DeviceSession.objects.filter(id=session.id))
        return Response({
            "message": "Device logged out successfully.",
            "logged_out_current_device": current,
        }, status=status.HTTP_200_OK)

    def post(self, request, session_id):
        return self.delete(request, session_id)


class LogoutOtherDevicesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        revoked = _revoke_sessions(
            DeviceSession.objects.filter(user=request.user).exclude(
                id=_current_session_id(request)
            )
        )
        return Response({
            "message": "Other devices logged out successfully.",
            "revoked_sessions": revoked,
            "current_session_active": True,
        }, status=status.HTTP_200_OK)


class LogoutAllDevicesView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        revoked = _revoke_sessions(DeviceSession.objects.filter(
            user=request.user
        ))
        return Response({
            "message": "All devices logged out successfully.",
            "revoked_sessions": revoked,
            "current_session_active": False,
        }, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]
        if not user.check_password(old_password):
            return Response(
                {"error": "Old password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logout_all = _boolean_option(request, "logout_all_devices")
        logout_others = _boolean_option(request, "logout_other_devices")

        with transaction.atomic():
            user.set_password(new_password)
            user.save(update_fields=["password"])
            if logout_all:
                revoked = _revoke_sessions(
                    DeviceSession.objects.filter(user=user)
                )
            elif logout_others:
                revoked = _revoke_sessions(
                    DeviceSession.objects.filter(user=user).exclude(
                        id=_current_session_id(request)
                    )
                )
            else:
                revoked = 0

        return Response({
            "message": "Password updated successfully.",
            "revoked_sessions": revoked,
            "current_session_active": not logout_all,
        }, status=status.HTTP_200_OK)
