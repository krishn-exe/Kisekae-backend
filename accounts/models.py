from decimal import Decimal
from django.conf import settings
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


class UserSettings(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="settings",
    )
    email_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(
        default=True,
        help_text="Master toggle for mobile push notifications via Firebase.",
    )
    order_alerts = models.BooleanField(default=True)
    stock_alerts = models.BooleanField(default=True)
    low_stock_total_alerts = models.BooleanField(
        default=True,
        help_text="Alert when total product stock across all variants falls below 250 units.",
    )
    low_stock_variant_alerts = models.BooleanField(
        default=True,
        help_text="Alert when an individual variant size stock falls below 20 units.",
    )
    language = models.CharField(max_length=10, default="en")
    timezone = models.CharField(max_length=50, default="Asia/Kolkata")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings for {self.user.email}"


class AddressTitle(models.TextChoices):
    HOME = "Home", "Home"
    OFFICE = "Office", "Office"
    OTHER = "Other", "Other"


class Address(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    title = models.CharField(
        max_length=50,
        default=AddressTitle.HOME,
        help_text="Title or label for the address, e.g. Home, Office, Other.",
    )
    recipient_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Name of recipient for delivery.",
    )
    phone = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Contact phone number for delivery.",
    )
    address_line_1 = models.CharField(
        max_length=255,
        help_text="Street address, building, or house number (required).",
    )
    address_line_2 = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Apartment, suite, unit, floor, landmark (optional).",
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="City or town.",
    )
    state = models.CharField(
        max_length=100,
        help_text="State, province, or region (required).",
    )
    postal_code = models.CharField(
        max_length=20,
        help_text="Postal code or PIN code (required).",
    )
    country = models.CharField(
        max_length=100,
        default="India",
        help_text="Country name.",
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Geographical latitude coordinate.",
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Geographical longitude coordinate.",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Whether this is the user's primary/default address.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]
        verbose_name = "Address"
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.title}: {self.address_line_1}, {self.postal_code} ({self.user.email})"

    def save(self, *args, **kwargs):
        # If this is the user's first address, mark it as default automatically
        if not self.pk and not Address.objects.filter(user=self.user).exists():
            self.is_default = True

        super().save(*args, **kwargs)

        # If marked as default, unset default for all other addresses of this user
        if self.is_default:
            Address.objects.filter(user=self.user, is_default=True).exclude(pk=self.pk).update(is_default=False)


class WishlistItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wishlist_items",
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="wishlisted_by",
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional user note or remark for this item.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"],
                name="unique_user_wishlist_product",
            ),
        ]
        verbose_name = "Wishlist Item"
        verbose_name_plural = "Wishlist Items"

    def __str__(self):
        return f"{self.user.email} - {self.product.name}"


class PendingRegistration(models.Model):
    email = models.EmailField(
        unique=True,
        validators=[email_validator],
    )
    name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        validators=[name_validator],
    )
    password = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Hashed password for the pending user.",
    )
    is_seller = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Pending Registration"
        verbose_name_plural = "Pending Registrations"

    def is_expired(self, ttl_seconds: int = 900) -> bool:
        from django.utils import timezone
        import datetime
        return timezone.now() > self.created_at + datetime.timedelta(seconds=ttl_seconds)

    def remaining_ttl(self, ttl_seconds: int = 900) -> int:
        from django.utils import timezone
        import datetime
        expires_at = self.created_at + datetime.timedelta(seconds=ttl_seconds)
        rem = int((expires_at - timezone.now()).total_seconds())
        return max(rem, 0)

    def __str__(self):
        return f"Pending registration for {self.email}"


class Wallet(models.Model):
    BALANCE_LIMIT = Decimal("10000.00")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
    )
    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Current wallet balance in INR (max limit 10,000).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Wallet"
        verbose_name_plural = "Wallets"

    def __str__(self):
        return f"Wallet({self.user.email}) - ₹{self.balance}"


class WalletTransactionType(models.TextChoices):
    CREDIT = "CREDIT", "Credit"
    DEBIT = "DEBIT", "Debit"
    REFUND = "REFUND", "Refund"


class WalletTransaction(models.Model):
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    txn_type = models.CharField(max_length=10, choices=WalletTransactionType.choices)
    order = models.ForeignKey(
        "orders.Order",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_transactions",
    )
    razorpay_payment_id = models.CharField(max_length=100, blank=True, default="")
    razorpay_order_id = models.CharField(max_length=100, blank=True, default="")
    description = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Wallet Transaction"
        verbose_name_plural = "Wallet Transactions"

    def __str__(self):
        return f"{self.txn_type} ₹{self.amount} ({self.wallet.user.email})"


