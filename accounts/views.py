import logging
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from drf_spectacular.utils import OpenApiExample, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from allauth.socialaccount.providers.github.views import GitHubOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter

from .otp import RedisOTP
from .responses import error_response, success_response
from .serializers import (
    ChangePasswordSerializer,
    GitHubOAuthSerializer,
    GoogleOAuthSerializer,
    LoginPasswordSerializer,
    LogoutSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
)
from .social import process_social_login

logger = logging.getLogger(__name__)
User = get_user_model()


def tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


COMMON_ERROR_SCHEMA = inline_serializer(
    name="ErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="ErrorEnvelopeDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)


class RegisterView(APIView):

    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Register a new user account",
        description="Creates a new user with mandatory email. Name and password are optional (OTP-only accounts). Accepts optional `is_seller` boolean (defaults to false).",
        request=RegisterSerializer,
        responses={
            201: inline_serializer(
                name="RegisterResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="RegisteredUserData",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(),
                            "is_seller": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="RegisterRequestExample",
                summary="Valid Registration Request",
                value={
                    "name": "Krishn Sharma",
                    "email": "user@example.com",
                    "password": "StrongPassword123!",
                    "is_seller": False,
                },
                request_only=True,
            ),
            OpenApiExample(
                name="RegisterSuccessExample",
                summary="Successful Registration (201 Created)",
                value={
                    "success": True,
                    "message": "User registered successfully",
                    "data": {
                        "id": 123,
                        "name": "Krishn Sharma",
                        "email": "user@example.com",
                        "is_seller": False,
                    },
                },
                response_only=True,
                status_codes=["201"],
            ),
            OpenApiExample(
                name="RegisterValidationErrorExample",
                summary="Validation Error (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Validation failed",
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "details": {
                            "email": ["An account with this email already exists."],
                        },
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return success_response(
            message="User registered successfully",
            data={
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_seller": user.is_seller,
            },
            status_code=status.HTTP_201_CREATED,
        )


class LoginPasswordView(APIView):

    permission_classes = [AllowAny]
    serializer_class = LoginPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log in with email and password",
        description=(
            "Authenticates with email and password. "
            "Returns user info in the response body. "
            "Tokens are delivered in headers: `Authorization: Bearer <access_token>` "
            "and `X-Refresh-Token: <refresh_token>`."
        ),
        request=LoginPasswordSerializer,
        responses={
            200: inline_serializer(
                name="LoginSuccessResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="LoginUserData",
                        fields={
                            "user": inline_serializer(
                                name="LoginUserDetail",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(),
                                },
                            ),
                        },
                    ),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="LoginRequestExample",
                summary="Valid Login Request",
                value={
                    "email": "user@example.com",
                    "password": "StrongPassword123!",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="LoginSuccessExample",
                summary="Successful Login (200 OK)",
                value={
                    "success": True,
                    "message": "Login successful",
                    "data": {
                        "user": {
                            "id": 123,
                            "name": "Krishn Sharma",
                            "email": "user@example.com",
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="LoginErrorExample",
                summary="Invalid Credentials (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Incorrect password.",
                    "error": {
                        "code": "INVALID_CREDENTIALS",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        return success_response(
            message="Login successful",
            data={
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                }
            },
            tokens=tokens_for_user(user),
            status_code=status.HTTP_200_OK,
        )


class OTPRequestView(APIView):

    permission_classes = [AllowAny]
    serializer_class = OTPRequestSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Request a 6-digit OTP code",
        description=(
            "Generates and sends a 6-digit one-time code to the specified email address. "
            "Rate limited to once every 60 seconds per email. "
            "Set `purpose` to 'verify_email' for email verification, 'password_reset' for forgot-password flow (defaults to 'login')."
        ),
        request=OTPRequestSerializer,
        responses={
            200: inline_serializer(
                name="OTPRequestResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            429: COMMON_ERROR_SCHEMA,
            503: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="OTPRequestExample",
                summary="OTP Request Example",
                value={
                    "email": "user@example.com",
                    "purpose": "login",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="OTPRequestSuccessExample",
                summary="OTP Code Dispatched (200 OK)",
                value={
                    "success": True,
                    "message": "A code has been sent.",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="OTPRequestRateLimitExample",
                summary="Cooldown In Effect (429 Too Many Requests)",
                value={
                    "success": False,
                    "message": "Please wait 45 seconds before requesting a new code.",
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "details": {"wait_seconds": 45},
                    },
                },
                response_only=True,
                status_codes=["429"],
            ),
        ],
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        purpose = serializer.validated_data.get("purpose", "login")

        user = User.objects.filter(email__iexact=email).first()

        if user and user.is_active:
            if purpose == "password_reset" and not user.is_email_verified:
                pass
            else:
                otp = RedisOTP(email=email, purpose=purpose)
                can_send, wait_secs = otp.can_issue()
                if not can_send:
                    return error_response(
                        message=f"Please wait {wait_secs} seconds before requesting a new code.",
                        code="RATE_LIMIT_EXCEEDED",
                        details={"wait_seconds": wait_secs},
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    )

                raw_code = otp.issue()
                try:
                    self._send_code(user.email, raw_code, purpose)
                except Exception as e:
                    logger.exception("Failed to send OTP code to %s: %s", email, e)
                    return error_response(
                        message="Unable to send verification code. Please try again later.",
                        code="SERVICE_UNAVAILABLE",
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

        return success_response(
            message="If an account exists, a code has been sent.",
            data=None,
            status_code=status.HTTP_200_OK,
        )

    @staticmethod
    def _send_code(target_email, raw_code, purpose="login"):
        if purpose == "password_reset":
            subject = "Your Kisekae password reset code"
            action_desc = "password reset"
        elif purpose == "verify_email":
            subject = "Your Kisekae email verification code"
            action_desc = "email verification"
        else:
            subject = "Your Kisekae login code"
            action_desc = "login"

        body = f"Your {action_desc} code is {raw_code}. It expires in 5 minutes."
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[target_email],
        )


class OTPVerifyView(APIView):

    permission_classes = [AllowAny]
    serializer_class = OTPVerifySerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Verify OTP code and authenticate, verify email, or obtain password reset token",
        description=(
            "Verifies the 6-digit OTP code against Redis.\n"
            "- If `purpose` is 'verify_email', marks the user's email as verified and issues JWT tokens in headers.\n"
            "- If `purpose` is 'login' (default), marks email as verified and issues JWT tokens in headers: "
            "`Authorization: Bearer <access_token>` and `X-Refresh-Token: <refresh_token>`.\n"
            "- If `purpose` is 'password_reset', returns a 5-minute single-use `reset_token` in the response body "
            "to be used with `POST /accounts/password/reset/`."
        ),
        request=OTPVerifySerializer,
        responses={
            200: inline_serializer(
                name="OTPVerifyResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="OTPVerifyData",
                        fields={
                            "user": inline_serializer(
                                name="OTPVerifiedUser",
                                required=False,
                                allow_null=True,
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(),
                                },
                            ),
                            "reset_token": serializers.CharField(required=False, allow_null=True),
                        },
                    ),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="OTPVerifyLoginRequestExample",
                summary="OTP Verification Request (Login)",
                value={
                    "email": "user@example.com",
                    "code": "123456",
                    "purpose": "login",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="OTPVerifyPasswordResetRequestExample",
                summary="OTP Verification Request (Password Reset)",
                value={
                    "email": "user@example.com",
                    "code": "123456",
                    "purpose": "password_reset",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="OTPVerifyLoginSuccessExample",
                summary="Successful Verification for Login (200 OK)",
                value={
                    "success": True,
                    "message": "Verification successful",
                    "data": {
                        "user": {
                            "id": 123,
                            "name": "Krishn Sharma",
                            "email": "user@example.com",
                        },
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="OTPVerifyPasswordResetSuccessExample",
                summary="Successful Verification for Password Reset (200 OK)",
                value={
                    "success": True,
                    "message": "OTP verified successfully. Use the reset token to set a new password.",
                    "data": {
                        "reset_token": "wE9fXz2b_example_reset_token_xyz123",
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="OTPVerifyInvalidExample",
                summary="Invalid/Expired Code (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid or expired code.",
                    "error": {
                        "code": "INVALID_OTP",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        code = serializer.validated_data["code"]
        purpose = serializer.validated_data.get("purpose")

        if not purpose:
            if cache.get(f"otp:verify_email:{email}") and not cache.get(f"otp:login:{email}"):
                purpose = "verify_email"
            elif cache.get(f"otp:password_reset:{email}") and not cache.get(f"otp:login:{email}"):
                purpose = "password_reset"
            else:
                purpose = "login"

        otp = RedisOTP(email=email, purpose=purpose)
        if not otp.verify(code):
            return error_response(
                message="Invalid or expired code.",
                code="INVALID_OTP",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return error_response(
                message="No account found with that email address.",
                code="USER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if purpose == "password_reset":
            if not user.is_email_verified:
                return error_response(
                    message="Email is not verified.",
                    code="EMAIL_NOT_VERIFIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )
            reset_token = secrets.token_urlsafe(32)
            cache.set(
                f"password_reset_token:{reset_token}",
                {"user_id": user.id, "email": user.email},
                timeout=300,
            )
            return success_response(
                message="OTP verified successfully. Use the reset token to set a new password.",
                data={
                    "reset_token": reset_token,
                },
                status_code=status.HTTP_200_OK,
            )

        if purpose == "verify_email":
            if not user.is_email_verified:
                user.is_email_verified = True
                user.save(update_fields=["is_email_verified"])
            return success_response(
                message="Email verified successfully.",
                data={
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                    }
                },
                tokens=tokens_for_user(user),
                status_code=status.HTTP_200_OK,
            )

        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])

        return success_response(
            message="Verification successful",
            data={
                "user": {
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                }
            },
            tokens=tokens_for_user(user),
            status_code=status.HTTP_200_OK,
        )


class LogoutView(APIView):

    permission_classes = [AllowAny]
    serializer_class = LogoutSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log out user and blacklist refresh token",
        description=(
            "Logs out the user by blacklisting their refresh token. "
            "Pass the refresh token via `X-Refresh-Token` header or `refresh` body param."
        ),
        request=LogoutSerializer,
        responses={
            200: inline_serializer(
                name="LogoutSuccessResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="LogoutRequestExample",
                summary="Logout Request",
                value={
                    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.example_refresh_token",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="LogoutSuccessExample",
                summary="Logout Success (200 OK)",
                value={
                    "success": True,
                    "message": "Successfully logged out.",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="LogoutErrorExample",
                summary="Invalid Refresh Token (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid or expired refresh token",
                    "error": {
                        "code": "INVALID_TOKEN",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        if "refresh" not in data and "HTTP_X_REFRESH_TOKEN" in request.META:
            data["refresh"] = request.META["HTTP_X_REFRESH_TOKEN"]

        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return success_response(
            message="Successfully logged out.",
            data=None,
            status_code=status.HTTP_200_OK,
        )


class TokenRefreshView(SimpleJWTTokenRefreshView):

    @extend_schema(
        tags=["Accounts"],
        summary="Refresh JWT access token",
        description=(
            "Refreshes JWT tokens. Accepts refresh token from `X-Refresh-Token` header or `refresh` body param. "
            "Returns newly issued tokens in response headers: `Authorization: Bearer <access_token>` "
            "and `X-Refresh-Token: <refresh_token>`."
        ),
        request=inline_serializer(
            name="TokenRefreshRequest",
            fields={"refresh": serializers.CharField(required=False)},
        ),
        responses={
            200: inline_serializer(
                name="TokenRefreshResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="TokenRefreshRequestExample",
                summary="Token Refresh Request",
                value={
                    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.example_refresh_token",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="TokenRefreshSuccessExample",
                summary="Token Refreshed (200 OK)",
                value={
                    "success": True,
                    "message": "Token refreshed successfully",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="TokenRefreshErrorExample",
                summary="Invalid/Expired Token (401 Unauthorized)",
                value={
                    "success": False,
                    "message": "Token is invalid",
                    "error": {
                        "code": "INVALID_TOKEN",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    def post(self, request, *args, **kwargs):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        if "refresh" not in data and "HTTP_X_REFRESH_TOKEN" in request.META:
            data["refresh"] = request.META["HTTP_X_REFRESH_TOKEN"]

        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0]) from e
        tokens = serializer.validated_data

        response = success_response(
            message="Token refreshed successfully",
            data=None,
            status_code=status.HTTP_200_OK,
        )
        if "access" in tokens:
            response["Authorization"] = f"Bearer {tokens['access']}"
        refresh = tokens.get("refresh") or data.get("refresh")
        if refresh:
            response["X-Refresh-Token"] = str(refresh)
        response["Access-Control-Expose-Headers"] = "Authorization, X-Refresh-Token"
        return response


class ChangePasswordView(APIView):

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Change password (authenticated)",
        description=(
            "Allows an authenticated user to change their password by providing old and new passwords. "
            "The access token must be valid and sent via `Authorization: Bearer <token>`."
        ),
        request=ChangePasswordSerializer,
        responses={
            200: inline_serializer(
                name="ChangePasswordResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="ChangePasswordRequestExample",
                summary="Change Password Request",
                value={
                    "old_password": "OldStrongPassword123!",
                    "new_password": "NewStrongPassword456!",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="ChangePasswordSuccessExample",
                summary="Password Changed (200 OK)",
                value={
                    "success": True,
                    "message": "Password changed successfully.",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="ChangePasswordWrongOldExample",
                summary="Incorrect Old Password (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Old password is incorrect.",
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "details": {
                            "old_password": ["Old password is incorrect."],
                        },
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        if not request.user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not request.user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            message="Password changed successfully.",
            data=None,
            status_code=status.HTTP_200_OK,
        )


class ResetPasswordView(APIView):

    permission_classes = [AllowAny]
    serializer_class = ResetPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Reset password using reset token (unauthenticated)",
        description=(
            "Resets the password for an unauthenticated user using the single-use reset token "
            "obtained from `POST /accounts/otp/verify/` with `purpose='password_reset'`. "
            "Pass the `token` and `new_password`."
        ),
        request=ResetPasswordSerializer,
        responses={
            200: inline_serializer(
                name="ResetPasswordResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="ResetPasswordRequestExample",
                summary="Reset Password Request",
                value={
                    "token": "wE9fXz2b_example_reset_token_xyz123",
                    "new_password": "NewStrongPassword456!",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="ResetPasswordSuccessExample",
                summary="Password Reset Success (200 OK)",
                value={
                    "success": True,
                    "message": "Password reset successfully. Please login with your new password.",
                    "data": None,
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="ResetPasswordInvalidTokenExample",
                summary="Invalid or Expired Token (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid or expired reset token.",
                    "error": {
                        "code": "INVALID_TOKEN",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        new_password = serializer.validated_data["new_password"]

        cache_key = f"password_reset_token:{token}"
        token_data = cache.get(cache_key)

        if not token_data:
            return error_response(
                message="Invalid or expired reset token.",
                code="INVALID_TOKEN",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        user_id = token_data.get("user_id") if isinstance(token_data, dict) else token_data
        user = User.objects.filter(id=user_id).first()
        if not user:
            return error_response(
                message="No account found for this reset token.",
                code="USER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if not user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken,
        )

        outstanding_tokens = OutstandingToken.objects.filter(user=user)
        existing_ids = set(
            BlacklistedToken.objects.filter(token__in=outstanding_tokens).values_list(
                "token_id", flat=True
            )
        )
        to_create = [
            BlacklistedToken(token=t)
            for t in outstanding_tokens
            if t.id not in existing_ids
        ]
        if to_create:
            BlacklistedToken.objects.bulk_create(to_create, ignore_conflicts=True)

        cache.delete(cache_key)

        return success_response(
            message="Password reset successfully. Please login with your new password.",
            data=None,
            status_code=status.HTTP_200_OK,
        )


class BaseOAuthView(APIView):
    permission_classes = [AllowAny]
    adapter_class = None
    provider_name = ""

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        code = serializer.validated_data.get("code")
        callback_url = serializer.validated_data.get("callback_url")
        access_token = serializer.validated_data.get("access_token")

        try:
            user, created, provider_name = process_social_login(
                request=request,
                adapter_class=self.adapter_class,
                code=code,
                callback_url=callback_url,
                access_token=access_token,
            )

            if not user.is_active:
                return error_response(
                    message="This account is inactive.",
                    code="ACCOUNT_INACTIVE",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            if not user.is_email_verified:
                return error_response(
                    message="Email is not verified.",
                    code="EMAIL_NOT_VERIFIED",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

            status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            message = (
                f"Account created and authenticated via {provider_name}"
                if created
                else f"{provider_name} authentication successful"
            )
            return success_response(
                message=message,
                data={
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                    },
                    "created": created,
                },
                tokens=tokens_for_user(user),
                status_code=status_code,
            )
        except ValueError as exc:
            return error_response(
                message=str(exc),
                code=f"INVALID_{self.provider_name.upper()}_AUTH",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.exception("Unexpected error in %s OAuth: %s", self.provider_name, exc)
            return error_response(
                message=f"Authentication with {self.provider_name} failed.",
                code="AUTHENTICATION_FAILED",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GoogleOAuthView(BaseOAuthView):
    serializer_class = GoogleOAuthSerializer
    adapter_class = GoogleOAuth2Adapter
    provider_name = "Google"

    @extend_schema(
        tags=["Accounts"],
        summary="Google OAuth2 login and registration",
        description=(
            "Authenticates or registers a user via Google OAuth2 using django-allauth. "
            "Accepts an authorization `code` (and optional `callback_url`). "
            "Returns user info in the response body. "
            "Tokens are delivered in headers: `Authorization: Bearer <access_token>` "
            "and `X-Refresh-Token: <refresh_token>`."
        ),
        request=GoogleOAuthSerializer,
        responses={
            200: inline_serializer(
                name="GoogleOAuthResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="GoogleOAuthData",
                        fields={
                            "user": inline_serializer(
                                name="GoogleUserSummary",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(allow_null=True),
                                },
                            ),
                            "created": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            201: inline_serializer(
                name="GoogleOAuthCreatedResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="GoogleOAuthCreatedData",
                        fields={
                            "user": inline_serializer(
                                name="GoogleCreatedUserSummary",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(allow_null=True),
                                },
                            ),
                            "created": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            500: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="GoogleOAuthRequestExample",
                summary="Google OAuth Request",
                value={
                    "code": "4/0AdQt8ug_example_google_auth_code_xyz123",
                    "callback_url": "kisekae://auth/google/callback",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="GoogleOAuthLoginSuccessExample",
                summary="Existing User Login (200 OK)",
                value={
                    "success": True,
                    "message": "Google authentication successful",
                    "data": {
                        "user": {
                            "id": 123,
                            "name": "Jane Google",
                            "email": "user@example.com",
                        },
                        "created": False,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="GoogleOAuthRegisterSuccessExample",
                summary="New User Created (201 Created)",
                value={
                    "success": True,
                    "message": "Account created and authenticated via Google",
                    "data": {
                        "user": {
                            "id": 124,
                            "name": "Jane Google",
                            "email": "newuser@example.com",
                        },
                        "created": True,
                    },
                },
                response_only=True,
                status_codes=["201"],
            ),
            OpenApiExample(
                name="GoogleOAuthErrorExample",
                summary="Invalid Code (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid or expired Google authorization code.",
                    "error": {
                        "code": "INVALID_GOOGLE_AUTH",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        return super().post(request)


class GitHubOAuthView(BaseOAuthView):
    serializer_class = GitHubOAuthSerializer
    adapter_class = GitHubOAuth2Adapter
    provider_name = "GitHub"

    @extend_schema(
        tags=["Accounts"],
        summary="GitHub OAuth2 login and registration",
        description=(
            "Authenticates or registers a user via GitHub OAuth2 using django-allauth. "
            "Accepts an authorization `code` (and optional `callback_url`). "
            "Returns user info in the response body. "
            "Tokens are delivered in headers: `Authorization: Bearer <access_token>` "
            "and `X-Refresh-Token: <refresh_token>`."
        ),
        request=GitHubOAuthSerializer,
        responses={
            200: inline_serializer(
                name="GitHubOAuthResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="GitHubOAuthData",
                        fields={
                            "user": inline_serializer(
                                name="GitHubUserSummary",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(allow_null=True),
                                },
                            ),
                            "created": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            201: inline_serializer(
                name="GitHubOAuthCreatedResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="GitHubOAuthCreatedData",
                        fields={
                            "user": inline_serializer(
                                name="GitHubCreatedUserSummary",
                                fields={
                                    "id": serializers.IntegerField(),
                                    "name": serializers.CharField(),
                                    "email": serializers.EmailField(allow_null=True),
                                },
                            ),
                            "created": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            500: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="GitHubOAuthRequestExample",
                summary="GitHub OAuth Request",
                value={
                    "code": "gho_12345678abcdef_example_github_auth_code",
                    "callback_url": "kisekae://auth/github/callback",
                },
                request_only=True,
            ),
            OpenApiExample(
                name="GitHubOAuthLoginSuccessExample",
                summary="Existing User Login (200 OK)",
                value={
                    "success": True,
                    "message": "GitHub authentication successful",
                    "data": {
                        "user": {
                            "id": 123,
                            "name": "Jane GitHub",
                            "email": "user@example.com",
                        },
                        "created": False,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="GitHubOAuthRegisterSuccessExample",
                summary="New User Created (201 Created)",
                value={
                    "success": True,
                    "message": "Account created and authenticated via GitHub",
                    "data": {
                        "user": {
                            "id": 124,
                            "name": "Jane GitHub",
                            "email": "newuser@example.com",
                        },
                        "created": True,
                    },
                },
                response_only=True,
                status_codes=["201"],
            ),
            OpenApiExample(
                name="GitHubOAuthErrorExample",
                summary="Invalid Code (400 Bad Request)",
                value={
                    "success": False,
                    "message": "Invalid or expired GitHub authorization code.",
                    "error": {
                        "code": "INVALID_GITHUB_AUTH",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    def post(self, request):
        return super().post(request)


class UserDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Accounts"],
        summary="Get current user details",
        description="Returns the authenticated user's details.",
        responses={
            200: inline_serializer(
                name="UserDetailResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="UserDetailData",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(allow_null=True),
                            "is_email_verified": serializers.BooleanField(),
                            "is_seller": serializers.BooleanField(),
                        },
                    ),
                },
            ),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        },
        examples=[
            OpenApiExample(
                name="UserDetailSuccessExample",
                summary="Current User Profile (200 OK)",
                value={
                    "success": True,
                    "message": "User profile fetched successfully",
                    "data": {
                        "id": 123,
                        "name": "Krishn Sharma",
                        "email": "user@example.com",
                        "is_email_verified": True,
                        "is_seller": False,
                    },
                },
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                name="UserDetailUnauthenticatedExample",
                summary="Unauthenticated Request (401 Unauthorized)",
                value={
                    "success": False,
                    "message": "Authentication credentials were not provided.",
                    "error": {
                        "code": "NOT_AUTHENTICATED",
                        "details": None,
                    },
                },
                response_only=True,
                status_codes=["401"],
            ),
        ],
    )
    def get(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if not user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return success_response(
            message="User profile fetched successfully",
            data={
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_email_verified": user.is_email_verified,
                "is_seller": user.is_seller,
            },
            status_code=status.HTTP_200_OK,
        )