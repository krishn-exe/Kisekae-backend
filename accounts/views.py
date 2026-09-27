import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .otp import RedisOTP, send_otp_whatsapp
from .serializers import (
    ChangePasswordSerializer,
    LoginPasswordSerializer,
    LogoutSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
)
from .utils import get_user_by_identifier

logger = logging.getLogger(__name__)
User = get_user_model()


def tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class RegisterView(APIView):
    """Create a new user account with email and/or phone. Password is optional for OTP-only accounts."""

    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Register a new user account",
        description="Creates a new user with email and/or phone. Password is optional (OTP-only accounts).",
        request=RegisterSerializer,
        responses={
            201: inline_serializer(
                name="RegisterResponse",
                fields={
                    "user": inline_serializer(
                        name="RegisteredUserSummary",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(allow_null=True),
                            "phone": serializers.CharField(allow_null=True),
                        },
                    ),
                    "tokens": inline_serializer(
                        name="AuthTokenPair",
                        fields={
                            "refresh": serializers.CharField(),
                            "access": serializers.CharField(),
                        },
                    ),
                },
            ),
            400: OpenApiResponse(description="Validation error (e.g. weak password or already registered)"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "user": {"id": user.id, "name": user.name, "email": user.email, "phone": user.phone},
                "tokens": tokens_for_user(user),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginPasswordView(APIView):
    """Authenticate with email or phone number and password, returning JWT tokens."""

    permission_classes = [AllowAny]
    serializer_class = LoginPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log in with email/phone and password",
        description="Authenticates with email or phone number and password, returning JWT access and refresh tokens.",
        request=LoginPasswordSerializer,
        responses={
            200: inline_serializer(
                name="LoginSuccessResponse",
                fields={
                    "tokens": inline_serializer(
                        name="TokenPair",
                        fields={
                            "refresh": serializers.CharField(),
                            "access": serializers.CharField(),
                        },
                    )
                },
            ),
            400: OpenApiResponse(description="Invalid credentials or inactive account"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        return Response({"tokens": tokens_for_user(user)}, status=status.HTTP_200_OK)


class OTPRequestView(APIView):
    """Send a 6-digit OTP code via email or WhatsApp for login or password reset."""

    permission_classes = [AllowAny]
    serializer_class = OTPRequestSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Request a 6-digit OTP code",
        description=(
            "Generates and sends a 6-digit one-time code via email or WhatsApp (Meta Cloud API). "
            "Rate limited to once every 60 seconds per identifier. "
            "Set `purpose` to 'password_reset' for forgot-password flow (defaults to 'login'). "
            "In development mode (DEBUG=True), the generated OTP is included in the response as `debug_otp`."
        ),
        request=OTPRequestSerializer,
        responses={
            200: inline_serializer(
                name="OTPRequestResponse",
                fields={
                    "detail": serializers.CharField(),
                    "debug_otp": serializers.CharField(required=False),
                },
            ),
            400: OpenApiResponse(description="Invalid identifier format"),
            429: OpenApiResponse(description="Cooldown in effect — please wait 60 seconds"),
            503: OpenApiResponse(description="Delivery provider unavailable"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"]
        purpose = serializer.validated_data.get("purpose", "login")

        user, channel = get_user_by_identifier(identifier)

        raw_code = None
        if user and user.is_active:
            otp = RedisOTP(identifier=identifier, purpose=purpose)
            can_send, wait_secs = otp.can_issue()
            if not can_send:
                return Response(
                    {"detail": f"Please wait {wait_secs} seconds before requesting a new code."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            raw_code = otp.issue()
            if settings.DEBUG:
                logger.info("DEBUG OTP for %s (%s): %s", identifier, purpose, raw_code)
            try:
                send_target = user.email if channel == "email" else (user.phone or identifier)
                self._send_code(channel, send_target, raw_code, purpose)
            except Exception as e:
                logger.exception("Failed to send OTP code to %s: %s", identifier, e)
                return Response(
                    {"detail": "Unable to send verification code. Please try again later."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        resp_data = {"detail": "If an account exists, a code has been sent."}
        if settings.DEBUG and raw_code:
            resp_data["debug_otp"] = raw_code
        return Response(resp_data, status=status.HTTP_200_OK)

    @staticmethod
    def _send_code(channel, target, raw_code, purpose="login"):
        subject = "Your Kisekae password reset code" if purpose == "password_reset" else "Your Kisekae login code"
        body = f"Your {'password reset' if purpose == 'password_reset' else 'login'} code is {raw_code}. It expires in 5 minutes."
        if channel == "email":
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target],
            )
        else:
            send_otp_whatsapp(target, raw_code)


class OTPVerifyView(APIView):
    """Verify a 6-digit OTP code and receive JWT tokens. Also marks the email or phone as verified."""

    permission_classes = [AllowAny]
    serializer_class = OTPVerifySerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Verify OTP code and authenticate",
        description="Verifies the 6-digit OTP code against Redis. On success, issues JWT tokens and marks the channel as verified.",
        request=OTPVerifySerializer,
        responses={
            200: inline_serializer(
                name="OTPVerifyResponse",
                fields={
                    "tokens": inline_serializer(
                        name="VerifiedTokenPair",
                        fields={
                            "refresh": serializers.CharField(),
                            "access": serializers.CharField(),
                        },
                    )
                },
            ),
            400: OpenApiResponse(description="Invalid or expired code"),
            403: OpenApiResponse(description="Account is inactive"),
            404: OpenApiResponse(description="No account found with that identifier"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"]
        code = serializer.validated_data["code"]

        otp = RedisOTP(identifier=identifier, purpose="login")
        if not otp.verify(code):
            return Response({"detail": "Invalid or expired code."}, status=status.HTTP_400_BAD_REQUEST)

        user, channel = get_user_by_identifier(identifier)
        if not user:
            return Response({"detail": "No account found with that identifier."}, status=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            return Response({"detail": "This account is inactive."}, status=status.HTTP_403_FORBIDDEN)

        if channel == "email" and not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])
        elif channel == "phone" and not user.is_phone_verified:
            user.is_phone_verified = True
            user.save(update_fields=["is_phone_verified"])

        return Response({"tokens": tokens_for_user(user)}, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """Log out by blacklisting the access token on Redis. Both access and refresh tokens are validated."""

    permission_classes = [AllowAny]
    serializer_class = LogoutSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log out user and blacklist access token",
        description=(
            "Logs out the user by placing their access token on a Redis blacklist with its remaining TTL. "
            "The access token can be provided either in the `Authorization: Bearer <access_token>` header "
            "or in the request body under `access`. Both access and refresh tokens are validated to ensure "
            "they belong to the same user and the access token is not already blacklisted."
        ),
        request=LogoutSerializer,
        responses={
            200: inline_serializer(
                name="LogoutSuccessResponse",
                fields={
                    "detail": serializers.CharField(),
                },
            ),
            400: OpenApiResponse(
                description="Validation error (e.g. mismatched users, wrong token types, already blacklisted, or missing tokens)"
            ),
            401: OpenApiResponse(description="Invalid or expired token"),
        },
    )
    def post(self, request):
        auth_header = request.headers.get("Authorization", "")
        header_token = None
        if auth_header.startswith("Bearer "):
            header_token = auth_header.split(" ", 1)[1].strip()

        serializer = self.serializer_class(
            data=request.data,
            context={"header_token": header_token},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    """Authenticated password change: requires valid (non-blacklisted) access token."""

    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Change password (authenticated)",
        description=(
            "Allows an authenticated user to change their password by providing old and new passwords. "
            "The access token must be valid and not blacklisted. "
            "After success, the user's existing sessions remain valid — use the logout endpoint to revoke them."
        ),
        request=ChangePasswordSerializer,
        responses={
            200: inline_serializer(
                name="ChangePasswordResponse",
                fields={"detail": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Validation error (e.g. wrong old password, weak new password)"),
            401: OpenApiResponse(description="Unauthenticated or blacklisted token"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password changed successfully."}, status=status.HTTP_200_OK)


class ResetPasswordView(APIView):
    """Unauthenticated password reset: verifies OTP then sets new password."""

    permission_classes = [AllowAny]
    serializer_class = ResetPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Reset password via OTP (unauthenticated)",
        description=(
            "Resets the password for an unauthenticated user. "
            "First request an OTP via `POST /accounts/otp/request/`, then submit the identifier, "
            "OTP code, and new password here. Does NOT return tokens — user must login again manually."
        ),
        request=ResetPasswordSerializer,
        responses={
            200: inline_serializer(
                name="ResetPasswordResponse",
                fields={"detail": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Invalid/expired OTP code or weak password"),
            404: OpenApiResponse(description="No account found with that identifier"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        identifier = serializer.validated_data["identifier"]
        code = serializer.validated_data["code"]
        new_password = serializer.validated_data["new_password"]

        otp = RedisOTP(identifier=identifier, purpose="password_reset")
        if not otp.verify(code):
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, _ = get_user_by_identifier(identifier)
        if not user:
            return Response(
                {"detail": "No account found with that identifier."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            return Response(
                {"detail": "This account is inactive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password reset successfully. Please login with your new password."},
            status=status.HTTP_200_OK,
        )