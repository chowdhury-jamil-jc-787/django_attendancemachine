from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed, TokenError
from .models import BlacklistedAccessToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
import jwt

class CustomJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        try:
            decoded = jwt.decode(raw_token, options={"verify_signature": False})
            token_type = decoded.get("token_type")
        except Exception:
            raise AuthenticationFailed("Invalid token format.")

        try:
            if token_type == "access":
                token = AccessToken(raw_token)
            elif token_type == "refresh":
                token = RefreshToken(raw_token)
            else:
                raise AuthenticationFailed("Unsupported token type")
        except TokenError:
            raise AuthenticationFailed("Your token has been blacklisted.")

        # Manual blacklist check for access tokens
        jti = token.get("jti")
        if token_type == "access" and BlacklistedAccessToken.objects.filter(jti=jti).exists():
            raise AuthenticationFailed("Your token has been blacklisted.")

        return token