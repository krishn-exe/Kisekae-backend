import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


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

    def restore(self):
        if self.status != StoreStatus.ARCHIVED:
            return
        self.status = self.archived_from_status or StoreStatus.ACTIVE
        self.archived_from_status = None
        self.archived_at = None
        self.save(update_fields=["status", "archived_from_status", "archived_at", "updated_at"])

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or "store"
            slug = base_slug
            counter = 1
            while Store.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


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
