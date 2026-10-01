from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models

from .utils import email_validator, name_validator, password_validator


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email=None, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set.")

        email = self.normalize_email(email).strip().lower()
        user = self.model(email=email, **extra_fields)

        if password is not None:
            password_validator(password)
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
    is_seller = models.BooleanField(default=False)

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