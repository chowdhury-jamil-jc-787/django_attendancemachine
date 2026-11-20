
import uuid
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .models import BlacklistedAccessToken, DeviceSession


class CustomJWTAuthentication(JWTAuthentication):
    """
    Custom JWT authentication with device session validation.

    Features:
    - Validate JWT access tokens.
    - Check blacklisted access tokens.
    - Validate active device sessions.
    - Reject revoked or expired sessions.
    - Track device activity.
    """

    def get_validated_token(self, raw_token):
        # Step 1: Validate JWT signature, expiry and token type.
        try:
            token = AccessToken(raw_token)
        except TokenError as exc:
            raise InvalidToken(
                "Invalid or expired access token."
            ) from exc

        # Step 2: Check token blacklist.
        jti = token.get("jti")

        if not jti:
            raise AuthenticationFailed(
                "Invalid token identifier.",
                code="invalid_token"
            )

        if BlacklistedAccessToken.objects.filter(
            jti=jti
        ).exists():
            raise AuthenticationFailed(
                "Your token has been blacklisted.",
                code="token_blacklisted"
            )

        return token

    def get_user(self, validated_token):
        # Step 3: Validate user using SimpleJWT.
        user = super().get_user(validated_token)

        # Step 4: Get device session ID from JWT.
        session_id = validated_token.get("sid")

        if not session_id:
            raise AuthenticationFailed(
                "Device session not found. Please log in again.",
                code="session_required"
            )

        # Step 5: Validate the session UUID.
        try:
            session_uuid = uuid.UUID(str(session_id))
        except (ValueError, TypeError, AttributeError):
            raise AuthenticationFailed(
                "Invalid device session.",
                code="invalid_session"
            )

        # Step 6: Check whether the device session is active.
        now = timezone.now()

        session = DeviceSession.objects.filter(
            id=session_uuid,
            user=user,
            revoked_at__isnull=True,
            expires_at__gt=now
        ).first()

        if session is None:
            raise AuthenticationFailed(
                "This device has been logged out or its session has expired. Please log in again.",
                code="session_expired"
            )

        # Step 7: Update last activity, at most once every 5 minutes.
        # This avoids unnecessary database writes on every request.
        activity_threshold = now - timedelta(minutes=5)

        if session.last_seen_at < activity_threshold:
            DeviceSession.objects.filter(
                id=session.id,
                revoked_at__isnull=True,
                last_seen_at__lt=activity_threshold
            ).update(
                last_seen_at=now
            )

        return user
