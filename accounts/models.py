import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .utils import email_validator, name_validator


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email=None, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set.")

        email = self.normalize_email(email).strip().lower()
        user = self.model(email=email, **extra_fields)

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(
        max_length=150,
        blank=True,
        validators=[name_validator],
    )
    email = models.EmailField(
        unique=True,
        validators=[email_validator],
    )

    is_email_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def clean(self):
        super().clean()
        if self.name:
            self.name = self.name.strip()
            name_validator(self.name)

        if not self.email:
            raise ValidationError("Email is required.")
        self.email = self.email.strip().lower()
        email_validator(self.email)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()

        if self.email:
            self.email = self.email.strip().lower()

        super().save(*args, **kwargs)

    def __str__(self):
        return self.email or f"user #{self.pk}"


class RefreshToken(models.Model):
    """
    Server-side opaque refresh token model.
    The raw token is returned to the client and never stored.
    Only the SHA-256 hash is saved in the database.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="refresh_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    is_revoked = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_revoked"]),
        ]

    def __str__(self):
        return f"RefreshToken(user_id={self.user_id}, revoked={self.is_revoked})"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.is_revoked and not self.is_expired

    @classmethod
    def hash_token(cls, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def create_token(cls, user, lifetime_days: int | None = None) -> str:
        """
        Creates a new DB-backed refresh token for the user.
        Returns the raw secret string.
        """
        if lifetime_days is None:
            lifetime_days = getattr(settings, "REFRESH_TOKEN_LIFETIME_DAYS", 7)
        raw_token = secrets.token_urlsafe(48)
        token_hash = cls.hash_token(raw_token)
        expires_at = timezone.now() + timedelta(days=lifetime_days)
        cls.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        return raw_token

    @classmethod
    def resolve(cls, raw_token: str):
        """
        Looks up a refresh token by its raw string.
        Returns the RefreshToken instance (with user joined) or None.
        """
        if not raw_token or not isinstance(raw_token, str):
            return None
        token_hash = cls.hash_token(raw_token.strip())
        return cls.objects.select_related("user").filter(token_hash=token_hash).first()

    @classmethod
    def revoke(cls, raw_token: str) -> bool:
        """
        Marks a single refresh token as revoked.
        Returns True if a token was found and updated, False otherwise.
        """
        if not raw_token or not isinstance(raw_token, str):
            return False
        token_hash = cls.hash_token(raw_token.strip())
        updated = cls.objects.filter(token_hash=token_hash, is_revoked=False).update(is_revoked=True)
        return updated > 0

    @classmethod
    def revoke_all_for_user(cls, user) -> int:
        """
        Revokes all active refresh tokens for a user.
        """
        return cls.objects.filter(user=user, is_revoked=False).update(is_revoked=True)