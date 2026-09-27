from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken

from .token_blacklist import is_access_token_blacklisted


class RedisJWTAuthentication(JWTAuthentication):

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        jti = token.get("jti")
        if jti and is_access_token_blacklisted(jti):
            raise InvalidToken({"detail": "Token is blacklisted", "code": "token_not_valid"})
        return token


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class RedisJWTAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = "accounts.authentication.RedisJWTAuthentication"
        name = "jwtAuth"

        def get_security_requirement(self, auto_schema):
            return {self.name: []}

        def get_security_definition(self, auto_schema):
            return {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
except ImportError:
    pass

