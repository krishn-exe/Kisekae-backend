try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension

    class SimpleJWTAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = "rest_framework_simplejwt.authentication.JWTAuthentication"
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

