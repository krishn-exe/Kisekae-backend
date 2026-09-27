import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .otp import RedisOTP
from .serializers import (
    LoginPasswordSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    RegisterSerializer,
)
from .utils import get_user_by_identifier

logger = logging.getLogger(__name__)
User = get_user_model()


def tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
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
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        return Response({"tokens": tokens_for_user(user)}, status=status.HTTP_200_OK)


class OTPRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"]

        user, channel = get_user_by_identifier(identifier)

        if user and user.is_active:
            otp = RedisOTP(identifier=identifier, purpose="login")
            can_send, wait_secs = otp.can_issue()
            if not can_send:
                return Response(
                    {"detail": f"Please wait {wait_secs} seconds before requesting a new code."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )

            raw_code = otp.issue()
            try:
                send_target = user.email if channel == "email" else (user.phone or identifier)
                self._send_code(channel, send_target, raw_code)
            except Exception as e:
                logger.exception("Failed to send OTP code to %s: %s", identifier, e)
                return Response(
                    {"detail": "Unable to send verification code. Please try again later."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response({"detail": "If an account exists, a code has been sent."}, status=status.HTTP_200_OK)

    @staticmethod
    def _send_code(channel, target, raw_code):
        if channel == "email":
            send_mail(
                subject="Your Kisekae login code",
                message=f"Your login code is {raw_code}. It expires in 5 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target],
            )
        else:
            from .sms import send_otp_sms

            send_otp_sms(target, raw_code)


class OTPVerifyView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
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