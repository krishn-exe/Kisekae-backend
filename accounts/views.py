import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from .google import (
    get_or_create_google_user,
    verify_google_id_token,
)
from .models import RefreshToken
from .otp import RedisOTP
from .serializers import (
    ChangePasswordSerializer,
    GoogleAuthSerializer,
    LoginPasswordSerializer,
    LogoutSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    TokenRefreshSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def tokens_for_user(user):
    access = AccessToken.for_user(user)
    refresh = RefreshToken.create_token(user)
    return {"refresh": refresh, "access": str(access)}


class RegisterView(APIView):

    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Register a new user account",
        description="Creates a new user with mandatory email. Name and password are optional (OTP-only accounts).",
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
                            "email": serializers.EmailField(),
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
                "user": {"id": user.id, "name": user.name, "email": user.email},
                "tokens": tokens_for_user(user),
            },
            status=status.HTTP_201_CREATED,
        )


class LoginPasswordView(APIView):

    permission_classes = [AllowAny]
    serializer_class = LoginPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log in with email and password",
        description="Authenticates with email and password, returning JWT access and refresh tokens.",
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

    permission_classes = [AllowAny]
    serializer_class = OTPRequestSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Request a 6-digit OTP code",
        description=(
            "Generates and sends a 6-digit one-time code to the specified email address. "
            "Rate limited to once every 60 seconds per email. "
            "Set `purpose` to 'password_reset' for forgot-password flow (defaults to 'login')."
        ),
        request=OTPRequestSerializer,
        responses={
            200: inline_serializer(
                name="OTPRequestResponse",
                fields={
                    "detail": serializers.CharField(),
                },
            ),
            400: OpenApiResponse(description="Invalid email format"),
            429: OpenApiResponse(description="Cooldown in effect — please wait 60 seconds"),
            503: OpenApiResponse(description="Delivery provider unavailable"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        purpose = serializer.validated_data.get("purpose", "login")

        user = User.objects.filter(email__iexact=email).first()

        if user and user.is_active:
            otp = RedisOTP(email=email, purpose=purpose)
            can_send, wait_secs = otp.can_issue()
            if not can_send:
                return Response(
                    {"detail": f"Please wait {wait_secs} seconds before requesting a new code."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            raw_code = otp.issue()
            try:
                self._send_code(user.email, raw_code, purpose)
            except Exception as e:
                logger.exception("Failed to send OTP code to %s: %s", email, e)
                return Response(
                    {"detail": "Unable to send verification code. Please try again later."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response(
            {"detail": "If an account exists, a code has been sent."},
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _send_code(target_email, raw_code, purpose="login"):
        subject = "Your Kisekae password reset code" if purpose == "password_reset" else "Your Kisekae login code"
        body = f"Your {'password reset' if purpose == 'password_reset' else 'login'} code is {raw_code}. It expires in 5 minutes."
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
        summary="Verify OTP code and authenticate",
        description="Verifies the 6-digit OTP code against Redis. On success, issues JWT tokens and marks the email as verified.",
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
            404: OpenApiResponse(description="No account found with that email address"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        code = serializer.validated_data["code"]

        otp = RedisOTP(email=email, purpose="login")
        if not otp.verify(code):
            return Response({"detail": "Invalid or expired code."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response({"detail": "No account found with that email address."}, status=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            return Response({"detail": "This account is inactive."}, status=status.HTTP_403_FORBIDDEN)

        if not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])

        return Response({"tokens": tokens_for_user(user)}, status=status.HTTP_200_OK)


class TokenRefreshView(APIView):

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = TokenRefreshSerializer
    www_authenticate_realm = "api"

    def get_authenticate_header(self, request):
        return f'Bearer realm="{self.www_authenticate_realm}"'

    @extend_schema(
        tags=["Accounts"],
        summary="Refresh an access token",
        description=(
            "Refreshes an access token using an active DB-backed refresh token. "
            "Rotates the refresh token upon successful use."
        ),
        request=TokenRefreshSerializer,
        responses={
            200: inline_serializer(
                name="TokenRefreshResponse",
                fields={
                    "access": serializers.CharField(),
                    "refresh": serializers.CharField(),
                },
            ),
            400: OpenApiResponse(description="Validation error (e.g. missing refresh token)"),
            401: OpenApiResponse(description="Invalid, expired, or revoked refresh token"),
            403: OpenApiResponse(description="Account is inactive"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        token_obj = serializer.validated_data["token_obj"]

        # Revoke old refresh token upon rotation
        token_obj.is_revoked = True
        token_obj.save(update_fields=["is_revoked"])

        # Issue new token pair
        access = AccessToken.for_user(token_obj.user)
        new_refresh = RefreshToken.create_token(token_obj.user)

        return Response(
            {
                "access": str(access),
                "refresh": new_refresh,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):

    authentication_classes = []
    permission_classes = [AllowAny]
    serializer_class = LogoutSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log out user and blacklist access token",
        description=(
            "Logs out the user by placing their access token on a Redis blacklist with its remaining TTL "
            "and marking the refresh token as revoked in the database. "
            "The access token must be provided in the `Authorization: Bearer <access_token>` header. "
            "The refresh token is provided in the request body under `refresh`. "
            "Both access and refresh tokens are validated to ensure they belong to the same user "
            "and neither token has already been revoked or blacklisted."
        ),
        auth=[{"jwtAuth": []}],
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

    permission_classes = [AllowAny]
    serializer_class = ResetPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Reset password via OTP (unauthenticated)",
        description=(
            "Resets the password for an unauthenticated user. "
            "First request an OTP via `POST /accounts/otp/request/`, then submit the email, "
            "OTP code, and new password here. Does NOT return tokens — user must login again manually."
        ),
        request=ResetPasswordSerializer,
        responses={
            200: inline_serializer(
                name="ResetPasswordResponse",
                fields={"detail": serializers.CharField()},
            ),
            400: OpenApiResponse(description="Invalid/expired OTP code or weak password"),
            404: OpenApiResponse(description="No account found with that email address"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        code = serializer.validated_data["code"]
        new_password = serializer.validated_data["new_password"]

        otp = RedisOTP(email=email, purpose="password_reset")
        if not otp.verify(code):
            return Response(
                {"detail": "Invalid or expired code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return Response(
                {"detail": "No account found with that email address."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not user.is_active:
            return Response(
                {"detail": "This account is inactive."},
                status=status.HTTP_403_FORBIDDEN,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])
        RefreshToken.revoke_all_for_user(user)

        return Response(
            {"detail": "Password reset successfully. Please login with your new password."},
            status=status.HTTP_200_OK,
        )


class GoogleAuthView(APIView):

    permission_classes = [AllowAny]
    serializer_class = GoogleAuthSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Google OAuth login and registration",
        description=(
            "Authenticates or registers a user via Google. "
            "Accepts a Google `id_token` (JWT) from Google Identity Services or One Tap. "
            "If the user exists by email, logs them in. "
            "If the user is new, automatically registers them with their Google profile details. "
            "Returns standard JWT access/refresh tokens."
        ),
        request=GoogleAuthSerializer,
        responses={
            200: inline_serializer(
                name="GoogleAuthResponse",
                fields={
                    "user": inline_serializer(
                        name="GoogleUserSummary",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(allow_null=True),
                        },
                    ),
                    "tokens": inline_serializer(
                        name="GoogleAuthTokens",
                        fields={
                            "refresh": serializers.CharField(),
                            "access": serializers.CharField(),
                        },
                    ),
                    "created": serializers.BooleanField(
                        help_text="True if a new user was created, False if existing user logged in."
                    ),
                },
            ),
            201: inline_serializer(
                name="GoogleAuthCreatedResponse",
                fields={
                    "user": inline_serializer(
                        name="GoogleCreatedUserSummary",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(allow_null=True),
                        },
                    ),
                    "tokens": inline_serializer(
                        name="GoogleAuthCreatedTokens",
                        fields={
                            "refresh": serializers.CharField(),
                            "access": serializers.CharField(),
                        },
                    ),
                    "created": serializers.BooleanField(),
                },
            ),
            400: OpenApiResponse(description="Invalid token, unverified email, or missing parameters"),
            403: OpenApiResponse(description="Account is inactive"),
            500: OpenApiResponse(description="Google token verification failed"),
        },
    )
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        id_token_str = serializer.validated_data["id_token"]

        try:
            payload = verify_google_id_token(id_token_str)
            user, created = get_or_create_google_user(payload)

            if not user.is_active:
                return Response(
                    {"detail": "This account is inactive."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return Response(
                {
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                    },
                    "tokens": tokens_for_user(user),
                    "created": created,
                },
                status=status_code,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Unexpected error in GoogleAuthView: %s", exc)
            return Response(
                {"detail": "Authentication with Google failed."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

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
                    "id": serializers.IntegerField(),
                    "name": serializers.CharField(),
                    "email": serializers.EmailField(allow_null=True),
                },
            ),
            401: OpenApiResponse(description="Unauthenticated or blacklisted token"),
        },
    )
    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
            },
            status=status.HTTP_200_OK,
        )