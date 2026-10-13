import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.text import slugify

from .validators import gst_validator, pan_validator, phone_validator


def store_logo_upload_path(instance, filename):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    return f"stores/{instance.id}/logo/{uuid.uuid4().hex}.{ext}"


def store_banner_upload_path(instance, filename):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    return f"stores/{instance.id}/banner/{uuid.uuid4().hex}.{ext}"


class StoreStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    SUSPENDED = "SUSPENDED", "Suspended"
    ARCHIVED = "ARCHIVED", "Archived"


class StoreRole(models.TextChoices):
    OWNER = "OWNER", "Owner"
    MANAGER = "MANAGER", "Manager"


class InvitationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    REVOKED = "REVOKED", "Revoked"
    EXPIRED = "EXPIRED", "Expired"


class VerificationStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class StoreQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=StoreStatus.ACTIVE)

    def not_archived(self):
        return self.exclude(status=StoreStatus.ARCHIVED)

    def for_user(self, user):
        return self.not_archived().filter(members=user)


class Store(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=180, unique=True)
    description = models.TextField(blank=True, default="")
    logo = models.ImageField(upload_to=store_logo_upload_path, null=True, blank=True)
    banner = models.ImageField(upload_to=store_banner_upload_path, null=True, blank=True)
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=20, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=StoreStatus.choices,
        default=StoreStatus.ACTIVE,
        db_index=True,
    )
    archived_from_status = models.CharField(
        max_length=20,
        choices=[
            (StoreStatus.ACTIVE, "Active"),
            (StoreStatus.INACTIVE, "Inactive"),
            (StoreStatus.SUSPENDED, "Suspended"),
        ],
        null=True,
        blank=True,
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_stores",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="StoreMembership",
        related_name="stores",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StoreQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == StoreStatus.ACTIVE

    @property
    def is_archived(self):
        return self.status == StoreStatus.ARCHIVED

    @property
    def is_business_verified(self):
        try:
            return bool(
                hasattr(self, "business_verification")
                and self.business_verification
                and self.business_verification.status == VerificationStatus.APPROVED
            )
        except Exception:
            return False

    @property
    def logo_url(self):
        if self.logo and hasattr(self.logo, "url"):
            return self.logo.url
        return None

    @property
    def banner_url(self):
        if self.banner and hasattr(self.banner, "url"):
            return self.banner.url
        return None

    def archive(self):
        if self.status == StoreStatus.ARCHIVED:
            return
        self.archived_from_status = self.status
        self.status = StoreStatus.ARCHIVED
        self.archived_at = timezone.now()
        self.save(update_fields=["status", "archived_from_status", "archived_at", "updated_at"])

    def suspend(self):
        if self.status == StoreStatus.SUSPENDED:
            return
        self.status = StoreStatus.SUSPENDED
        self.save(update_fields=["status", "updated_at"])
        self.invitations.filter(status=InvitationStatus.PENDING).update(
            status=InvitationStatus.REVOKED,
            responded_at=timezone.now(),
        )

    def restore(self):
        if self.status != StoreStatus.ARCHIVED:
            return
        self.status = self.archived_from_status or StoreStatus.ACTIVE
        self.archived_from_status = None
        self.archived_at = None
        self.save(update_fields=["status", "archived_from_status", "archived_at", "updated_at"])

    def _generate_candidate_slug(self, start_counter=0):
        base_slug = slugify(self.name) or "store"
        if start_counter == 0 and not Store.objects.filter(slug=base_slug).exclude(pk=self.pk).exists():
            return base_slug

        counter = max(1, start_counter)
        while Store.objects.filter(slug=f"{base_slug}-{counter}").exclude(pk=self.pk).exists():
            counter += 1
        return f"{base_slug}-{counter}"

    def save(self, *args, **kwargs):
        is_suspending = False
        if self.pk and self.status == StoreStatus.SUSPENDED:
            orig = Store.objects.filter(pk=self.pk).values("status").first()
            if orig and orig["status"] != StoreStatus.SUSPENDED:
                is_suspending = True

        if not self.slug:
            base_slug = slugify(self.name) or "store"
            counter = 0
            max_retries = 5
            saved = False
            for _ in range(max_retries):
                self.slug = self._generate_candidate_slug(start_counter=counter)
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    saved = True
                    break
                except IntegrityError as exc:
                    err_str = str(exc).lower()
                    if any(kw in err_str for kw in ["slug", "stores_store_slug_key", "unique constraint"]):
                        counter += 1
                        continue
                    raise
            if not saved:
                self.slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

        if is_suspending:
            self.invitations.filter(status=InvitationStatus.PENDING).update(
                status=InvitationStatus.REVOKED,
                responded_at=timezone.now(),
            )


class StoreInvitation(models.Model):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    email = models.EmailField(db_index=True)
    invitee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_store_invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_store_invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=InvitationStatus.choices,
        default=InvitationStatus.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "email"],
                condition=models.Q(status=InvitationStatus.PENDING),
                name="unique_pending_store_invitation",
            )
        ]

    def __str__(self):
        return f"Invite for {self.email} to {self.store.name} ({self.status})"

    @property
    def is_expired(self):
        return self.status == InvitationStatus.PENDING and timezone.now() > self.expires_at

    def check_and_mark_expired(self):
        if self.is_expired:
            self.status = InvitationStatus.EXPIRED
            self.save(update_fields=["status"])
            return True
        return False


class StoreMembership(models.Model):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="store_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=StoreRole.choices,
        default=StoreRole.MANAGER,
        db_index=True,
    )
    invitation = models.ForeignKey(
        StoreInvitation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "user"],
                name="unique_store_membership",
            ),
            models.UniqueConstraint(
                fields=["store"],
                condition=models.Q(role=StoreRole.OWNER),
                name="unique_store_owner",
            ),
        ]

    def __str__(self):
        return f"{self.user} - {self.store} ({self.role})"

    @property
    def is_owner(self):
        return self.role == StoreRole.OWNER

    @property
    def is_manager(self):
        return self.role == StoreRole.MANAGER

    def clean(self):
        super().clean()
        if self.user_id and not getattr(self.user, "is_seller", False):
            raise ValidationError({"user": "Only users with seller status can be members of a store."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class BusinessVerification(models.Model):
    store = models.OneToOneField(
        Store,
        on_delete=models.CASCADE,
        related_name="business_verification",
    )
    pan_number = models.CharField(
        max_length=10,
        validators=[pan_validator],
    )
    gst_number = models.CharField(
        max_length=15,
        validators=[gst_validator],
    )
    business_address = models.TextField()
    business_phone = models.CharField(
        max_length=20,
        validators=[phone_validator],
    )
    business_email = models.EmailField()
    status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.APPROVED,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True, default="")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_verifications",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Business Verification for {self.store.name} ({self.status})"

    def clean(self):
        super().clean()
        if self.pan_number:
            self.pan_number = self.pan_number.strip().upper()
        if self.gst_number:
            self.gst_number = self.gst_number.strip().upper()
        if self.business_email:
            self.business_email = self.business_email.strip().lower()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
