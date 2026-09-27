from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

from .utils import normalize_phone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email=None, phone=None, password=None, **extra_fields):
        if not email and not phone:
            raise ValueError("A user needs at least an email or a phone number.")

        email = email.strip().lower() if email else None
        if phone:
            norm_phone = normalize_phone(phone)
            phone = norm_phone if norm_phone else phone.strip()
        else:
            phone = None

        user = self.model(email=email, phone=phone, **extra_fields)

        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email=None, phone=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, phone, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)

    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email__isnull=False) | models.Q(phone__isnull=False),
                name="user_has_email_or_phone",
            )
        ]

    def clean(self):
        super().clean()
        if self.email == "":
            self.email = None
        elif self.email:
            self.email = self.email.strip().lower()

        if self.phone == "":
            self.phone = None
        elif self.phone:
            norm = normalize_phone(self.phone)
            if norm:
                self.phone = norm

        if not self.email and not self.phone:
            raise ValidationError("Provide at least an email or a phone number.")

    def save(self, *args, **kwargs):
        if self.email == "":
            self.email = None
        elif self.email:
            self.email = self.email.strip().lower()

        if self.phone == "":
            self.phone = None
        elif self.phone:
            norm = normalize_phone(self.phone)
            if norm:
                self.phone = norm

        super().save(*args, **kwargs)

    def __str__(self):
        return self.email or self.phone or f"user #{self.pk}"