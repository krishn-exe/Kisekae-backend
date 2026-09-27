from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import models
from rest_framework import serializers

from .utils import (
    get_user_by_identifier,
    identify_channel,
    normalize_identifier,
    normalize_phone,
)

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    confirm_password = serializers.CharField(write_only=True, required=False, allow_blank=False)

    class Meta:
        model = User
        fields = ["name", "email", "phone", "password", "confirm_password"]

    def validate(self, attrs):
        email = attrs.get("email")
        phone = attrs.get("phone")
        if not email and not phone:
            raise serializers.ValidationError("Provide at least an email or a phone number.")

        if email:
            email = email.strip().lower()
            if User.objects.filter(email__iexact=email).exists():
                raise serializers.ValidationError({"email": "An account with this email already exists."})
            attrs["email"] = email

        if phone:
            norm_phone = normalize_phone(phone)
            if not norm_phone:
                raise serializers.ValidationError({"phone": "Enter a valid phone number."})
            digits = "".join(ch for ch in phone if ch.isdigit())
            bare_10 = digits[-10:] if len(digits) >= 10 else digits
            if User.objects.filter(models.Q(phone=norm_phone) | models.Q(phone=bare_10)).exists():
                raise serializers.ValidationError({"phone": "An account with this phone number already exists."})
            attrs["phone"] = norm_phone

        password = attrs.get("password")
        confirm_password = attrs.pop("confirm_password", None)

        if password or confirm_password:
            if not password:
                raise serializers.ValidationError({"password": "Password is required when confirm_password is provided."})
            if not confirm_password:
                raise serializers.ValidationError({"confirm_password": "Please confirm your password."})
            if password != confirm_password:
                raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
            validate_password(password)

        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class LoginPasswordSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        identifier = attrs.get("identifier", "").strip()
        user, channel = get_user_by_identifier(identifier)
        if not user:
            raise serializers.ValidationError("No account found with that email or phone number.")

        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")

        if not user.has_usable_password():
            raise serializers.ValidationError(
                "This account doesn't have a password set. Use 'Send me a code' to sign in instead."
            )

        if not user.check_password(attrs["password"]):
            raise serializers.ValidationError("Incorrect password.")

        attrs["user"] = user
        return attrs


class OTPRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField()

    def validate_identifier(self, value):
        cleaned = value.strip()
        if identify_channel(cleaned) is None:
            raise serializers.ValidationError("Enter a valid email address or phone number.")
        return cleaned


class OTPVerifySerializer(serializers.Serializer):
    identifier = serializers.CharField()
    code = serializers.CharField(max_length=6, min_length=6)

    def validate_identifier(self, value):
        cleaned = value.strip()
        if identify_channel(cleaned) is None:
            raise serializers.ValidationError("Enter a valid email address or phone number.")
        return cleaned

    def validate_code(self, value):
        cleaned = value.strip()
        if not cleaned.isdigit() or len(cleaned) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit code.")
        return cleaned