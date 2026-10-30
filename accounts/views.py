import logging
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from allauth.socialaccount.providers.github.views import GitHubOAuth2Adapter
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from cart.services import CartService
from products.models import Product, ProductCategory

from .otp import RedisOTP
from .models import Address, PendingRegistration, UserSettings, WishlistItem
from .responses import error_response, success_response
from .serializers import (
    AddressSerializer,
    AddWishlistSerializer,
    BulkMoveWishlistToCartSerializer,
    ChangePasswordSerializer,
    GitHubOAuthSerializer,
    GoogleOAuthSerializer,
    LoginPasswordSerializer,
    LogoutSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    UpdateWishlistNoteSerializer,
    UserSettingsSerializer,
    UserProfileSerializer,
    UserUpdateSerializer,
    WishlistItemSerializer)
from .social import process_social_login
from .tasks import send_otp_email_task

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
                "code": serializers.CharField(help_text="Standardized error code identifying failure (e.g. VALIDATION_ERROR, INVALID_CREDENTIALS, NOT_FOUND)."),
                "details": serializers.JSONField(allow_null=True),
            }),
    })

USER_SETTINGS_RESPONSE_SCHEMA = inline_serializer(
    name="UserSettingsResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": UserSettingsSerializer(),
    })


class RegisterView(APIView):

    throttle_scope = "register"
    permission_classes = [AllowAny]
    serializer_class = RegisterSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Register a new user account",
        description=(
            "Initiates user registration and automatically dispatches a 6-digit verification code to the email address. "
            "Registration data is staged temporarily in the database with a 15-minute expiration; the user account is created "
            "upon successful OTP verification (`POST /accounts/otp/verify/`). "
            "Name and password are optional. Accepts optional `is_seller` boolean (defaults to false)."
        ),
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
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(),
                            "is_seller": serializers.BooleanField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            409: COMMON_ERROR_SCHEMA,
            429: COMMON_ERROR_SCHEMA,
            503: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"]
        name = data.get("name", "")
        password = data.get("password")
        is_seller = data.get("is_seller", False)

        if User.objects.filter(email__iexact=email).exists():
            return success_response(
                message="User registered successfully. A verification code has been sent to your email.",
                data={
                    "name": name,
                    "email": email,
                    "is_seller": is_seller,
                },
                status_code=status.HTTP_201_CREATED)

        hashed_password = make_password(password) if password else ""
        
        pending_reg = PendingRegistration.objects.filter(email__iexact=email).first()
        if pending_reg:
            if not pending_reg.is_expired(ttl_seconds=900):
                ttl = pending_reg.remaining_ttl(ttl_seconds=900)
                return error_response(
                    message=f"A registration is already pending for this email. Please wait {ttl} seconds or verify the code sent.",
                    code="REGISTRATION_PENDING",
                    details={"ttl": ttl},
                    status_code=status.HTTP_409_CONFLICT)
            else:
                pending_reg.name = name
                pending_reg.password = hashed_password
                pending_reg.is_seller = is_seller
                from django.utils import timezone
                pending_reg.created_at = timezone.now()
                pending_reg.save()
        else:
            PendingRegistration.objects.create(
                email=email,
                name=name,
                password=hashed_password,
                is_seller=is_seller,
            )

        otp = RedisOTP(email=email, purpose="verify_email")
        can_send, wait_secs = otp.can_issue()
        if not can_send:
            return error_response(
                message=f"Please wait {wait_secs} seconds before requesting a new code.",
                code="RATE_LIMIT_EXCEEDED",
                details={"wait_seconds": wait_secs},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS)

        raw_code = otp.issue()
        try:
            OTPRequestView._send_code(email, raw_code, purpose="verify_email")
        except Exception as e:
            logger.exception("Failed to send OTP code to %s: %s", email, e)
            return error_response(
                message="Unable to send verification code. Please try again later.",
                code="SERVICE_UNAVAILABLE",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        return success_response(
            message="User registered successfully. A verification code has been sent to your email.",
            data={
                "name": name,
                "email": email,
                "is_seller": is_seller,
            },
            status_code=status.HTTP_201_CREATED)


class LoginPasswordView(APIView):

    throttle_scope = "password_login"
    permission_classes = [AllowAny]
    serializer_class = LoginPasswordSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log in with email and password",
        description=(
            "Authenticates with email and password. "
            "Returns user info and JWT access token in the response body (`data.access`). "
            "The refresh token is delivered in a secure HttpOnly cookie (`refresh_token`)."
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(help_text="JWT access token"),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
        })
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
                    "is_seller": user.is_seller,
                }
            },
            tokens=tokens_for_user(user),
            status_code=status.HTTP_200_OK)


class OTPRequestView(APIView):

    throttle_scope = "otp_request"
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
                }),
            400: COMMON_ERROR_SCHEMA,
            429: COMMON_ERROR_SCHEMA,
            503: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        purpose = serializer.validated_data.get("purpose", "login")

        user = User.objects.filter(email__iexact=email).first()
        pending_reg = PendingRegistration.objects.filter(email__iexact=email).first()
        has_pending_reg = pending_reg is not None and not pending_reg.is_expired(ttl_seconds=900)

        should_send = False
        if user and user.is_active:
            if purpose == "password_reset" and not user.is_email_verified:
                should_send = False
            else:
                should_send = True
        elif has_pending_reg and purpose == "verify_email":
            should_send = True

        if should_send:
            otp = RedisOTP(email=email, purpose=purpose)
            can_send, wait_secs = otp.can_issue()
            if not can_send:
                return error_response(
                    message=f"Please wait {wait_secs} seconds before requesting a new code.",
                    code="RATE_LIMIT_EXCEEDED",
                    details={"wait_seconds": wait_secs},
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS)

            raw_code = otp.issue()
            target_email = user.email if user else email
            try:
                self._send_code(target_email, raw_code, purpose)
            except Exception as e:
                logger.exception("Failed to send OTP code to %s: %s", email, e)
                return error_response(
                    message="Unable to send verification code. Please try again later.",
                    code="SERVICE_UNAVAILABLE",
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

        return success_response(
            message="A code has been sent.",
            data=None,
            status_code=status.HTTP_200_OK)

    @staticmethod
    def _send_code(target_email, raw_code, purpose="login"):
        send_otp_email_task.delay(target_email, raw_code, purpose)


class OTPVerifyView(APIView):

    throttle_scope = "otp_verify"
    permission_classes = [AllowAny]
    serializer_class = OTPVerifySerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Verify OTP code and authenticate, verify email, or obtain password reset token",
        description=(
            "Verifies the 6-digit OTP code against Redis.\n"
            "- If `purpose` is 'verify_email', creates the database account (if pending registration) or marks email verified, "
            "returning user info (including `is_seller`) and JWT access token in `data.access`, with refresh token in an HttpOnly cookie (`refresh_token`). "
            "If the registration session expired (>15 min), returns 400 with code `REGISTRATION_EXPIRED`.\n"
            "- If `purpose` is 'login' (default), marks email verified and returns user info (including `is_seller`) and JWT access token in `data.access`, "
            "with refresh token in an HttpOnly cookie (`refresh_token`).\n"
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(required=False, allow_null=True, help_text="JWT access token"),
                            "reset_token": serializers.CharField(required=False, allow_null=True, help_text="Single-use password reset token"),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
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
                status_code=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first()

        if purpose == "verify_email":
            if user:
                if not user.is_active:
                    return error_response(
                        message="This account is inactive.",
                        code="ACCOUNT_INACTIVE",
                        status_code=status.HTTP_403_FORBIDDEN)
                if not user.is_email_verified:
                    user.is_email_verified = True
                    user.save(update_fields=["is_email_verified"])
                PendingRegistration.objects.filter(email__iexact=email).delete()
                return success_response(
                    message="Email verified successfully.",
                    data={
                        "user": {
                            "id": user.id,
                            "name": user.name,
                            "email": user.email,
                            "is_seller": user.is_seller,
                        }
                    },
                    tokens=tokens_for_user(user),
                    status_code=status.HTTP_200_OK)

            # User not in DB yet - check pending registration in DB
            pending_reg = PendingRegistration.objects.filter(email__iexact=email).first()
            if not pending_reg or pending_reg.is_expired(ttl_seconds=900):
                if pending_reg:
                    pending_reg.delete()
                return error_response(
                    message="Registration session expired or not found. Please register again.",
                    code="REGISTRATION_EXPIRED",
                    status_code=status.HTTP_400_BAD_REQUEST)

            user = User(
                email=pending_reg.email,
                name=pending_reg.name,
                is_seller=pending_reg.is_seller,
                is_email_verified=True,
                is_active=True)
            if pending_reg.password:
                user.password = pending_reg.password
            else:
                user.set_unusable_password()
            user.save()

            pending_reg.delete()

            return success_response(
                message="Email verified successfully.",
                data={
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                        "is_seller": user.is_seller,
                    }
                },
                tokens=tokens_for_user(user),
                status_code=status.HTTP_200_OK)

        if not user:
            return error_response(
                message="No account found with that email address.",
                code="USER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)

        if purpose == "password_reset":
            if not user.is_email_verified:
                return error_response(
                    message="Email is not verified.",
                    code="EMAIL_NOT_VERIFIED",
                    status_code=status.HTTP_403_FORBIDDEN)
            reset_token = secrets.token_urlsafe(32)
            cache.set(
                f"password_reset_token:{reset_token}",
                {"user_id": user.id, "email": user.email},
                timeout=300)
            return success_response(
                message="OTP verified successfully. Use the reset token to set a new password.",
                data={
                    "reset_token": reset_token,
                },
                status_code=status.HTTP_200_OK)

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
                    "is_seller": user.is_seller,
                }
            },
            tokens=tokens_for_user(user),
            status_code=status.HTTP_200_OK)


class LogoutView(APIView):

    throttle_scope = "logout"
    permission_classes = [AllowAny]
    serializer_class = LogoutSerializer

    @extend_schema(
        tags=["Accounts"],
        summary="Log out user and blacklist refresh token",
        description=(
            "Logs out the user by blacklisting their refresh token and clearing the `refresh_token` cookie. "
            "The refresh token is automatically read from the HttpOnly cookie, or can be passed via the `refresh` body param."
        ),
        request=LogoutSerializer,
        responses={
            200: inline_serializer(
                name="LogoutSuccessResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                }),
            400: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        if "refresh" not in data and "refresh_token" in request.COOKIES:
            data["refresh"] = request.COOKIES["refresh_token"]
        elif "refresh" not in data and "HTTP_X_REFRESH_TOKEN" in request.META:
            data["refresh"] = request.META["HTTP_X_REFRESH_TOKEN"]

        serializer = self.serializer_class(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        response = success_response(
            message="Successfully logged out.",
            data=None,
            status_code=status.HTTP_200_OK)
        response.delete_cookie(key="refresh_token", path="/")
        return response


class TokenRefreshView(SimpleJWTTokenRefreshView):

    throttle_scope = "token_refresh"

    @extend_schema(
        tags=["Accounts"],
        summary="Refresh JWT access token",
        description=(
            "Refreshes JWT access token. Accepts the refresh token automatically from the HttpOnly `refresh_token` cookie, "
            "or via optional `refresh` body param. Returns newly issued access token in the response body (`data.access`) "
            "and sets the rotated refresh token in the `refresh_token` HttpOnly cookie."
        ),
        request=inline_serializer(
            name="TokenRefreshRequest",
            fields={"refresh": serializers.CharField(required=False, help_text="Optional if refresh_token cookie is present")}),
        responses={
            200: inline_serializer(
                name="TokenRefreshResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="TokenRefreshData",
                        fields={
                            "access": serializers.CharField(help_text="Newly issued JWT access token"),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
        })
    def post(self, request, *args, **kwargs):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        if "refresh" not in data and "refresh_token" in request.COOKIES:
            data["refresh"] = request.COOKIES["refresh_token"]
        elif "refresh" not in data and "HTTP_X_REFRESH_TOKEN" in request.META:
            data["refresh"] = request.META["HTTP_X_REFRESH_TOKEN"]

        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0]) from e
        tokens = serializer.validated_data

        response = success_response(
            message="Token refreshed successfully",
            data={"access": tokens["access"]},
            status_code=status.HTTP_200_OK)
        refresh = tokens.get("refresh") or data.get("refresh")
        if refresh:
            response.set_cookie(
                key="refresh_token",
                value=str(refresh),
                httponly=True,
                samesite=getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax"),
                secure=getattr(settings, "AUTH_COOKIE_SECURE", getattr(settings, "SESSION_COOKIE_SECURE", False)),
                path="/")
        return response


class ChangePasswordView(APIView):

    throttle_scope = "password_change"
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
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        if not request.user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        if not request.user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN)

        serializer = self.serializer_class(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            message="Password changed successfully.",
            data=None,
            status_code=status.HTTP_200_OK)


class ResetPasswordView(APIView):

    throttle_scope = "password_reset"
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
                }),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
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
                status_code=status.HTTP_400_BAD_REQUEST)

        user_id = token_data.get("user_id") if isinstance(token_data, dict) else token_data
        user = User.objects.filter(id=user_id).first()
        if not user:
            return error_response(
                message="No account found for this reset token.",
                code="USER_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)

        if not user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN)

        user.set_password(new_password)
        user.save(update_fields=["password"])

        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
            OutstandingToken)

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
            status_code=status.HTTP_200_OK)


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
        code_verifier = serializer.validated_data.get("code_verifier")
        client_id = serializer.validated_data.get("client_id")
        id_token = serializer.validated_data.get("id_token")

        try:
            user, created, provider_name = process_social_login(
                request=request,
                adapter_class=self.adapter_class,
                code=code,
                callback_url=callback_url,
                access_token=access_token,
                code_verifier=code_verifier,
                client_id=client_id,
                id_token=id_token)

            if not user.is_active:
                return error_response(
                    message="This account is inactive.",
                    code="ACCOUNT_INACTIVE",
                    status_code=status.HTTP_403_FORBIDDEN)

            if not user.is_email_verified:
                return error_response(
                    message="Email is not verified.",
                    code="EMAIL_NOT_VERIFIED",
                    status_code=status.HTTP_403_FORBIDDEN)

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
                        "is_seller": user.is_seller,
                    },
                    "created": created,
                },
                tokens=tokens_for_user(user),
                status_code=status_code)
        except ValueError as exc:
            return error_response(
                message=str(exc),
                code=f"INVALID_{self.provider_name.upper()}_AUTH",
                status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception("Unexpected error in %s OAuth: %s", self.provider_name, exc)
            return error_response(
                message=f"Authentication with {self.provider_name} failed.",
                code="AUTHENTICATION_FAILED",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoogleOAuthView(BaseOAuthView):
    throttle_scope = "oauth_google"
    serializer_class = GoogleOAuthSerializer
    adapter_class = GoogleOAuth2Adapter
    provider_name = "Google"

    @extend_schema(
        tags=["Accounts"],
        summary="Google OAuth2 login and registration",
        description=(
            "Authenticates or registers a user via Google OAuth2.\n"
            "Supports both standard confidential web clients and mobile public clients (Android & iOS) via PKCE (RFC 7636).\n"
            "- For Web: accepts authorization `code` and optional `callback_url`.\n"
            "- For Mobile (PKCE): accepts authorization `code`, `code_verifier`, and optional `client_id` (Android/iOS) and `callback_url` without requiring a client secret.\n"
            "Returns user info and JWT access token in the response body (`data.access`). "
            "The refresh token is delivered in a secure HttpOnly cookie (`refresh_token`). "
            "Requires that the email address associated with the Google account is verified."
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(help_text="JWT access token"),
                            "created": serializers.BooleanField(),
                        }),
                }),
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(help_text="JWT access token"),
                            "created": serializers.BooleanField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            500: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        return super().post(request)


class GitHubOAuthView(BaseOAuthView):
    throttle_scope = "oauth_github"
    serializer_class = GitHubOAuthSerializer
    adapter_class = GitHubOAuth2Adapter
    provider_name = "GitHub"

    @extend_schema(
        tags=["Accounts"],
        summary="GitHub OAuth2 login and registration",
        description=(
            "Authenticates or registers a user via GitHub OAuth2 using django-allauth. "
            "Accepts an authorization `code` (and optional `callback_url`). "
            "Returns user info and JWT access token in the response body (`data.access`). "
            "The refresh token is delivered in a secure HttpOnly cookie (`refresh_token`). "
            "Requires that the email address associated with the GitHub account is verified on GitHub."
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(help_text="JWT access token"),
                            "created": serializers.BooleanField(),
                        }),
                }),
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
                                    "is_seller": serializers.BooleanField(),
                                }),
                            "access": serializers.CharField(help_text="JWT access token"),
                            "created": serializers.BooleanField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            500: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        return super().post(request)


class BecomeSellerView(APIView):
    """Upgrade the authenticated user's account to a seller account."""

    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Accounts"],
        summary="Become a seller",
        description=(
            "Upgrades the authenticated user's account to a seller account by setting `is_seller=True`. "
            "The user must have a verified email address. If the user is already a seller, "
            "a 200 response is returned with no changes."
        ),
        request=None,
        responses={
            200: inline_serializer(
                name="BecomeSellerResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="BecomeSellerData",
                        fields={
                            "id": serializers.IntegerField(),
                            "name": serializers.CharField(),
                            "email": serializers.EmailField(),
                            "is_seller": serializers.BooleanField(),
                        }),
                }),
            403: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        user = request.user

        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)

        if not user.is_email_verified:
            return error_response(
                message="Email must be verified before becoming a seller.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN)

        if user.is_seller:
            return success_response(
                message="You are already a seller.",
                data={
                    "id": user.id,
                    "name": user.name,
                    "email": user.email,
                    "is_seller": user.is_seller,
                },
                status_code=status.HTTP_200_OK)

        user.is_seller = True
        user.save(update_fields=["is_seller"])

        return success_response(
            message="Congratulations! Your account has been upgraded to a seller account.",
            data={
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "is_seller": user.is_seller,
            },
            status_code=status.HTTP_200_OK)


class UserDetailView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Get customer profile details",
        description="Returns the authenticated user's profile details including name, email, verification status, summary stats (addresses, wishlist, orders, saved AR looks), and settings.",
        responses={
            200: inline_serializer(
                name="UserDetailResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": UserProfileSerializer(),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def get(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        if not user.is_email_verified:
            return error_response(
                message="Email is not verified.",
                code="EMAIL_NOT_VERIFIED",
                status_code=status.HTTP_403_FORBIDDEN)
        return success_response(
            message="User profile fetched successfully.",
            data=UserProfileSerializer(user).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Update customer profile name",
        description="Updates the authenticated user's personal profile information (name).",
        request=UserUpdateSerializer,
        responses={
            200: inline_serializer(
                name="UserUpdateResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": UserProfileSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def put(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        serializer = UserUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user.name = serializer.validated_data["name"]
        user.save(update_fields=["name"])
        return success_response(
            message="User profile updated successfully.",
            data=UserProfileSerializer(user).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Delete / deactivate customer account",
        description="Deactivates the authenticated user's account and clears refresh token cookies.",
        responses={200: inline_serializer(name="UserDeleteResponse", fields={"message": serializers.CharField()})})
    def delete(self, request):
        user = request.user
        user.is_active = False
        user.save(update_fields=["is_active"])
        response = success_response(
            message="Account has been deactivated successfully.",
            data=None,
            status_code=status.HTTP_200_OK)
        response.delete_cookie(key="refresh_token", path="/")
        return response


class UserSettingsView(APIView):
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Get user notification and app settings",
        description="Retrieves the authenticated user's notification preferences and application settings.",
        responses={200: USER_SETTINGS_RESPONSE_SCHEMA})
    def get(self, request):
        settings_obj, _ = UserSettings.objects.get_or_create(user=request.user)
        return success_response(
            message="Settings fetched successfully.",
            data=UserSettingsSerializer(settings_obj).data)

    @extend_schema(
        tags=["Profile - Personal Info"],
        summary="Update user notification and app settings",
        description="Updates the authenticated user's notification preferences and application settings.",
        request=UserSettingsSerializer,
        responses={200: USER_SETTINGS_RESPONSE_SCHEMA, 400: COMMON_ERROR_SCHEMA})
    def put(self, request):
        settings_obj, _ = UserSettings.objects.get_or_create(user=request.user)
        serializer = UserSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return success_response(
            message="Settings updated successfully.",
            data=serializer.data)


# ============================================================================
# CUSTOMER ADDRESS VIEWS
# ============================================================================

class AddressListCreateView(APIView):
    """
    CRUD endpoint to list all addresses for the authenticated user, or create a new address.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_address_list",
        tags=["Profile - Addresses"],
        summary="List customer delivery addresses",
        description="Returns all saved addresses for the authenticated customer. Addresses are sorted with the default address first.",
        responses={
            200: inline_serializer(
                name="AddressListResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.ListSerializer(child=AddressSerializer()),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def get(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        addresses = user.addresses.all()
        serializer = AddressSerializer(addresses, many=True)
        return success_response(
            message="Addresses retrieved successfully.",
            data=serializer.data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_address_create",
        tags=["Profile - Addresses"],
        summary="Create a delivery address",
        description=(
            "Adds a new delivery address for the authenticated user. "
            "Address requires address_line_1, state, and postal_code (or pin_code). "
            "Optionally accepts title (Home/Office/Other), recipient_name, phone, address_line_2, city, latitude, and longitude. "
            "If is_default is true, or if this is the user's first address, it automatically becomes the default address."
        ),
        request=AddressSerializer,
        responses={
            201: inline_serializer(
                name="AddressCreateResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": AddressSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        serializer = AddressSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        address = serializer.save()
        return success_response(
            message="Address added successfully.",
            data=AddressSerializer(address).data,
            status_code=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    """
    CRUD endpoint to retrieve, update (PUT/PATCH), or delete a specific address.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    def _get_address(self, user, address_id):
        if not user.is_active:
            return None, error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        address = user.addresses.filter(pk=address_id).first()
        if not address:
            return None, error_response(
                message="Address not found.",
                code="ADDRESS_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        return address, None

    @extend_schema(
        operation_id="customer_address_retrieve",
        tags=["Profile - Addresses"],
        summary="Get address details",
        description="Retrieves a specific delivery address by ID belonging to the authenticated customer.",
        responses={
            200: inline_serializer(
                name="AddressDetailResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": AddressSerializer(),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def get(self, request, address_id):
        address, err = self._get_address(request.user, address_id)
        if err:
            return err
        return success_response(
            message="Address retrieved successfully.",
            data=AddressSerializer(address).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_address_replace",
        tags=["Profile - Addresses"],
        summary="Update address (full)",
        description="Replaces all fields of an existing delivery address.",
        request=AddressSerializer,
        responses={
            200: inline_serializer(
                name="AddressUpdateResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": AddressSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def put(self, request, address_id):
        address, err = self._get_address(request.user, address_id)
        if err:
            return err
        serializer = AddressSerializer(address, data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        return success_response(
            message="Address updated successfully.",
            data=AddressSerializer(updated).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_address_update",
        tags=["Profile - Addresses"],
        summary="Update address (partial)",
        description="Updates one or more fields of an existing delivery address.",
        request=AddressSerializer,
        responses={
            200: inline_serializer(
                name="AddressPatchResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": AddressSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def patch(self, request, address_id):
        address, err = self._get_address(request.user, address_id)
        if err:
            return err
        serializer = AddressSerializer(address, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        return success_response(
            message="Address updated successfully.",
            data=AddressSerializer(updated).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_address_destroy",
        tags=["Profile - Addresses"],
        summary="Delete address",
        description="Deletes a saved delivery address. If the deleted address was marked as default, the most recent remaining address is promoted to default.",
        responses={
            200: inline_serializer(
                name="AddressDeleteResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def delete(self, request, address_id):
        address, err = self._get_address(request.user, address_id)
        if err:
            return err
        was_default = address.is_default
        address.delete()
        if was_default:
            next_default = request.user.addresses.first()
            if next_default:
                next_default.is_default = True
                next_default.save(update_fields=["is_default", "updated_at"])
        return success_response(
            message="Address deleted successfully.",
            data=None,
            status_code=status.HTTP_200_OK)


class AddressSetDefaultView(APIView):
    """
    Convenience endpoint to set an address as the default shipping address.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_address_set_default",
        tags=["Profile - Addresses"],
        summary="Set address as default",
        description="Sets the specified address as the customer's primary default delivery address.",
        request=None,
        responses={
            200: inline_serializer(
                name="AddressSetDefaultResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": AddressSerializer(),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def post(self, request, address_id):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        address = user.addresses.filter(pk=address_id).first()
        if not address:
            return error_response(
                message="Address not found.",
                code="ADDRESS_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        address.is_default = True
        address.save()
        return success_response(
            message="Default address updated successfully.",
            data=AddressSerializer(address).data,
            status_code=status.HTTP_200_OK)


# ============================================================================
# CUSTOMER WISHLIST VIEWS
# ============================================================================

class WishlistListCreateView(APIView):
    """
    CRUD endpoint to view wishlist, add a product to wishlist, or clear wishlist.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_wishlist_list",
        tags=["Profile - Wishlist"],
        summary="List customer wishlist items",
        description="Returns products saved in the authenticated customer's wishlist. Filterable by category, in-stock presence, and price ordering.",
        parameters=[
            OpenApiParameter(name="category", description="Filter by product category", required=False, type=str, enum=[c.value for c in ProductCategory]),
            OpenApiParameter(name="in_stock", description="If true, only return wishlist items currently in stock", required=False, type=bool),
            OpenApiParameter(name="search", description="Search by product name", required=False, type=str),
            OpenApiParameter(name="ordering", description="Sort order for wishlist items", required=False, type=str, enum=["-created_at", "created_at", "price", "-price"]),
        ],
        responses={
            200: inline_serializer(
                name="WishlistListResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.ListSerializer(child=WishlistItemSerializer()),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def get(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)

        items = user.wishlist_items.select_related("product", "product__store").all()

        category = request.query_params.get("category")
        if category:
            items = items.filter(product__category=category.upper())

        in_stock = request.query_params.get("in_stock")
        if in_stock is not None:
            val = str(in_stock).strip().lower() in ("true", "1", "yes")
            if val:
                items = items.filter(product__stock__gt=0)
            else:
                items = items.filter(product__stock=0)

        search = request.query_params.get("search")
        if search:
            s_clean = search.strip()
            items = items.filter(product__name__icontains=s_clean)

        ordering = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-created_at": "-created_at",
            "created_at": "created_at",
            "newest": "-created_at",
            "oldest": "created_at",
            "price": "product__price",
            "-price": "-product__price",
        }
        items = items.order_by(valid_orderings.get(ordering, "-created_at"))

        serializer = WishlistItemSerializer(items, many=True)
        return success_response(
            message="Wishlist items retrieved successfully.",
            data=serializer.data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_wishlist_add",
        tags=["Profile - Wishlist"],
        summary="Add product to wishlist",
        description="Adds an active product to the authenticated customer's wishlist. If already present, updates its optional note and returns 200 OK.",
        request=AddWishlistSerializer,
        responses={
            201: inline_serializer(
                name="WishlistAddResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": WishlistItemSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        serializer = AddWishlistSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product_id = serializer.validated_data["product_id"]
        note = serializer.validated_data.get("note", "")

        product = Product.objects.select_related("store").get(pk=product_id)
        item, created = WishlistItem.objects.get_or_create(
            user=user,
            product=product,
            defaults={"note": note})
        if not created and note:
            item.note = note
            item.save(update_fields=["note"])

        if created and user.id:
            from products.tasks import recompute_user_taste_task
            transaction.on_commit(lambda: recompute_user_taste_task.delay(user.id))

        msg = "Product added to wishlist successfully." if created else "Product is already in your wishlist."
        stat = status.HTTP_201_CREATED if created else status.HTTP_200_OK

        return success_response(
            message=msg,
            data=WishlistItemSerializer(item).data,
            status_code=stat)

    @extend_schema(
        operation_id="customer_wishlist_clear",
        tags=["Profile - Wishlist"],
        summary="Clear wishlist",
        description="Removes all products from the customer's wishlist.",
        responses={
            200: inline_serializer(
                name="WishlistClearResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def delete(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        user.wishlist_items.all().delete()
        return success_response(
            message="Wishlist cleared successfully.",
            data=None,
            status_code=status.HTTP_200_OK)


class WishlistDetailView(APIView):
    """
    CRUD endpoint to inspect, update note, or remove a product from the user's wishlist.
    Accepts either product_id or wishlist item ID as the path parameter.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    def _get_item(self, user, product_id):
        return user.wishlist_items.select_related("product", "product__store").filter(
            Q(product_id=product_id) | Q(id=product_id)
        ).first()

    @extend_schema(
        operation_id="customer_wishlist_check",
        tags=["Profile - Wishlist"],
        summary="Check if product is wishlisted",
        description="Checks whether a given product is saved in the user's wishlist and returns the wishlist item details.",
        responses={
            200: inline_serializer(
                name="WishlistCheckResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="WishlistCheckData",
                        fields={
                            "is_in_wishlist": serializers.BooleanField(),
                            "item": WishlistItemSerializer(allow_null=True),
                        }),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
        })
    def get(self, request, product_id):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        item = self._get_item(user, product_id)
        if item:
            return success_response(
                message="Product is in your wishlist.",
                data={
                    "is_in_wishlist": True,
                    "item": WishlistItemSerializer(item).data,
                },
                status_code=status.HTTP_200_OK)
        return success_response(
            message="Product is not in your wishlist.",
            data={
                "is_in_wishlist": False,
                "item": None,
            },
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_wishlist_update_note",
        tags=["Profile - Wishlist"],
        summary="Update wishlist item note",
        description="Updates personal note or remark for this wishlisted item.",
        request=UpdateWishlistNoteSerializer,
        responses={
            200: inline_serializer(
                name="WishlistNoteUpdateResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": WishlistItemSerializer(),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def patch(self, request, product_id):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        item = self._get_item(user, product_id)
        if not item:
            return error_response(
                message="Product not found in your wishlist.",
                code="WISHLIST_ITEM_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        serializer = UpdateWishlistNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item.note = serializer.validated_data["note"]
        item.save(update_fields=["note"])
        return success_response(
            message="Wishlist item note updated successfully.",
            data=WishlistItemSerializer(item).data,
            status_code=status.HTTP_200_OK)

    @extend_schema(
        operation_id="customer_wishlist_remove",
        tags=["Profile - Wishlist"],
        summary="Remove product from wishlist",
        description="Deletes a product from the customer's wishlist by product ID or item ID.",
        responses={
            200: inline_serializer(
                name="WishlistRemoveResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": serializers.JSONField(allow_null=True),
                }),
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def delete(self, request, product_id):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        item = self._get_item(user, product_id)
        if not item:
            return error_response(
                message="Product not found in your wishlist.",
                code="WISHLIST_ITEM_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)
        item.delete()
        return success_response(
            message="Product removed from wishlist successfully.",
            data=None,
            status_code=status.HTTP_200_OK)


class WishlistMoveToCartView(APIView):
    """
    Convenience endpoint to move a wishlisted product into the shopping cart.
    Adds the product to the active cart and removes it from the wishlist.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_wishlist_move_to_cart",
        tags=["Profile - Wishlist"],
        summary="Move wishlisted product to cart",
        description="Transfers a wishlisted item into the user's active shopping cart and automatically deletes it from the wishlist.",
        request=None,
        responses={
            200: inline_serializer(
                name="WishlistMoveToCartResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="WishlistMoveToCartData",
                        fields={
                            "product_id": serializers.IntegerField(),
                            "cart_item_id": serializers.IntegerField(),
                            "cart_id": serializers.IntegerField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
            403: COMMON_ERROR_SCHEMA,
            404: COMMON_ERROR_SCHEMA,
        })
    def post(self, request, product_id):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)
        item = user.wishlist_items.select_related("product", "product__store").filter(
            Q(product_id=product_id) | Q(id=product_id)
        ).first()
        if not item:
            return error_response(
                message="Product not found in your wishlist.",
                code="WISHLIST_ITEM_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND)

        product = item.product
        try:
            cart = CartService.get_or_create_cart(user=user)
            cart_item = CartService.add_item(cart, product.id, quantity=1)
            item.delete()
        except ValidationError as exc:
            msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
            return error_response(
                message=msg,
                code="CART_ADDITION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST)

        return success_response(
            message=f"'{product.name}' moved to cart successfully.",
            data={
                "product_id": product.id,
                "cart_item_id": cart_item.id,
                "cart_id": cart.id,
            },
            status_code=status.HTTP_200_OK)



class WishlistBulkMoveToCartView(APIView):

    """
    Move wishlisted products into the user's shopping cart in bulk.
    If product_ids is omitted or empty, moves all items in the customer's wishlist.
    """
    throttle_scope = "user_detail"
    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="customer_wishlist_bulk_move_to_cart",
        tags=["Profile - Wishlist"],
        summary="Bulk move wishlist items to cart",
        description="Transfers multiple (or all) wishlisted items into the user's active shopping cart and automatically removes successfully added items from the wishlist.",
        request=BulkMoveWishlistToCartSerializer,
        responses={
            200: inline_serializer(
                name="WishlistBulkMoveToCartResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="WishlistBulkMoveToCartData",
                        fields={
                            "moved_count": serializers.IntegerField(),
                            "skipped_count": serializers.IntegerField(),
                            "moved_items": serializers.ListField(),
                            "skipped_items": serializers.ListField(),
                            "cart_id": serializers.IntegerField(allow_null=True),
                            "total_cart_items": serializers.IntegerField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        user = request.user
        if not user.is_active:
            return error_response(
                message="This account is inactive.",
                code="ACCOUNT_INACTIVE",
                status_code=status.HTTP_403_FORBIDDEN)

        serializer = BulkMoveWishlistToCartSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        product_ids = serializer.validated_data.get("product_ids", [])

        queryset = user.wishlist_items.select_related("product", "product__store").prefetch_related("product__sizes")
        if product_ids:
            queryset = queryset.filter(Q(product_id__in=product_ids) | Q(id__in=product_ids))

        wishlist_items = list(queryset)
        if not wishlist_items:
            return success_response(
                message="No matching wishlist items to move.",
                data={
                    "moved_count": 0,
                    "skipped_count": 0,
                    "moved_items": [],
                    "skipped_items": [],
                    "cart_id": None,
                    "total_cart_items": 0,
                })

        cart = CartService.get_or_create_cart(user=user)
        moved_items = []
        skipped_items = []

        for item in wishlist_items:
            product = item.product
            try:
                cart_item = CartService.add_item(cart, product.id, quantity=1)
                item.delete()
                moved_items.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "cart_item_id": cart_item.id,
                    "size": cart_item.size,
                })
            except ValidationError as exc:
                msg = exc.messages[0] if hasattr(exc, "messages") and exc.messages else str(exc)
                skipped_items.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "reason": msg,
                })

        cart.refresh_from_db()
        return success_response(
            message=f"{len(moved_items)} item(s) moved to cart successfully.",
            data={
                "moved_count": len(moved_items),
                "skipped_count": len(skipped_items),
                "moved_items": moved_items,
                "skipped_items": skipped_items,
                "cart_id": cart.id,
                "total_cart_items": cart.total_items,
            })


class WishlistBulkMoveToCartAliasView(WishlistBulkMoveToCartView):
    """Alias for bulk moving wishlist items to cart."""
    @extend_schema(
        operation_id="customer_wishlist_bulk_move_to_cart_alias",
        deprecated=True,
        tags=["Profile - Wishlist"],
        summary="Bulk move wishlist items to cart (alias)",
        description="Alias for POST /accounts/wishlist/move-to-cart/. Transfers multiple or all wishlisted items into active cart.",
        request=BulkMoveWishlistToCartSerializer,
        responses={
            200: inline_serializer(
                name="WishlistBulkMoveToCartAliasResponse",
                fields={
                    "success": serializers.BooleanField(),
                    "message": serializers.CharField(),
                    "data": inline_serializer(
                        name="WishlistBulkMoveToCartAliasData",
                        fields={
                            "moved_count": serializers.IntegerField(),
                            "skipped_count": serializers.IntegerField(),
                            "moved_items": serializers.ListField(),
                            "skipped_items": serializers.ListField(),
                            "cart_id": serializers.IntegerField(allow_null=True),
                            "total_cart_items": serializers.IntegerField(),
                        }),
                }),
            400: COMMON_ERROR_SCHEMA,
            401: COMMON_ERROR_SCHEMA,
        })
    def post(self, request):
        return super().post(request)
