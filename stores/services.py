from datetime import timedelta
import hashlib
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    InvitationStatus,
    Store,
    StoreInvitation,
    StoreMembership,
    StoreRole,
    StoreStatus,
)
from .tasks import send_store_invitation_email_task

User = get_user_model()


class StoreService:
    @staticmethod
    def _lock_store_for_membership_change(store):
        store = Store.objects.select_for_update().get(pk=store.pk)
        if store.status in {StoreStatus.SUSPENDED, StoreStatus.ARCHIVED}:
            raise ValidationError(
                f"Cannot change memberships while the store is {store.status.lower()}."
            )
        return store

    @staticmethod
    @transaction.atomic
    def create_store(user, name, description="", contact_email="", contact_phone=""):
        store = Store.objects.create(
            name=name,
            description=description,
            contact_email=contact_email,
            contact_phone=contact_phone,
            created_by=user,
        )
        membership = StoreMembership.objects.create(
            store=store,
            user=user,
            role=StoreRole.OWNER,
        )
        return store, membership

    @staticmethod
    def update_store(store, **data):
        update_fields = []
        for field, value in data.items():
            if hasattr(store, field):
                setattr(store, field, value)
                update_fields.append(field)
        if update_fields:
            update_fields.append("updated_at")
            store.save(update_fields=update_fields)
        return store

    @staticmethod
    @transaction.atomic
    def archive_store(store):
        store.archive()
        # Revoke any pending invitations for this store
        store.invitations.filter(status=InvitationStatus.PENDING).update(
            status=InvitationStatus.REVOKED,
            responded_at=timezone.now(),
        )
        return store

    @staticmethod
    def restore_store(store):
        store.restore()
        return store

    @staticmethod
    @transaction.atomic
    def transfer_ownership(store, current_owner, new_owner_member_id):
        store = StoreService._lock_store_for_membership_change(store)
        owner_membership = (
            StoreMembership.objects.select_for_update()
            .filter(store=store, user=current_owner, role=StoreRole.OWNER)
            .first()
        )
        if not owner_membership:
            raise ValidationError("Current user is not the owner of this store.")

        target_membership = (
            StoreMembership.objects.select_for_update()
            .select_related("user")
            .filter(store=store, id=new_owner_member_id)
            .first()
        )
        if not target_membership:
            raise ValidationError("Target member not found in this store.")
        if not target_membership.user.is_active:
            raise ValidationError("Cannot transfer ownership to an inactive user.")

        if target_membership.id == owner_membership.id:
            raise ValidationError("Cannot transfer ownership to yourself.")

        owner_membership.role = StoreRole.MANAGER
        owner_membership.save(update_fields=["role", "updated_at"])

        target_membership.role = StoreRole.OWNER
        target_membership.save(update_fields=["role", "updated_at"])

        return store

    @staticmethod
    @transaction.atomic
    def add_member(store, user, role=StoreRole.MANAGER):
        store = StoreService._lock_store_for_membership_change(store)
        if not user.is_seller:
            raise ValidationError("Only users with seller status can be members of a store.")
        if StoreMembership.objects.filter(store=store, user=user).exists():
            raise ValidationError("User is already a member of this store.")
        if role == StoreRole.OWNER:
            raise ValidationError("Cannot directly assign the OWNER role. Use transfer ownership.")

        return StoreMembership.objects.create(
            store=store,
            user=user,
            role=role,
        )

    @staticmethod
    @transaction.atomic
    def remove_member(store, member_id, actor_user):
        store = StoreService._lock_store_for_membership_change(store)
        membership = (
            StoreMembership.objects.select_related("user")
            .filter(store=store, id=member_id)
            .first()
        )
        if not membership:
            raise ValidationError("Member not found in this store.")
        if membership.role == StoreRole.OWNER:
            raise ValidationError("Cannot remove the store owner.")
        if membership.user_id == actor_user.id:
            raise ValidationError("Cannot remove yourself. Use the leave endpoint instead.")

        membership.delete()
        return True

    @staticmethod
    @transaction.atomic
    def leave_store(store, user):
        store = StoreService._lock_store_for_membership_change(store)
        membership = StoreMembership.objects.filter(store=store, user=user).first()
        if not membership:
            raise ValidationError("You are not a member of this store.")
        if membership.role == StoreRole.OWNER:
            raise ValidationError("The store owner cannot leave without transferring ownership first.")

        membership.delete()
        return True

    # --- INVITATION SERVICES ---

    @staticmethod
    @transaction.atomic
    def create_invitation(store, email, invited_by):
        email = email.strip().lower()

        if store.is_archived:
            raise ValidationError("Cannot invite members to an archived store.")
        if store.status == StoreStatus.SUSPENDED:
            raise ValidationError("Cannot invite members to a suspended store.")

        # Check if already a member
        if StoreMembership.objects.filter(store=store, user__email__iexact=email).exists():
            raise ValidationError("A user with this email is already a member of this store.")

        # Check for existing pending invitation
        pending_inv = StoreInvitation.objects.filter(
            store=store,
            email__iexact=email,
            status=InvitationStatus.PENDING,
        ).first()

        if pending_inv:
            if not pending_inv.is_expired:
                raise ValidationError("An invitation is already pending for this email address.")
            # If expired, mark as expired so we can create a fresh one
            pending_inv.status = InvitationStatus.EXPIRED
            pending_inv.save(update_fields=["status"])

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = timezone.now() + timedelta(days=getattr(settings, "STORE_INVITATION_TTL_DAYS", 7))
        target_user = User.objects.filter(email__iexact=email).first()

        invitation = StoreInvitation.objects.create(
            store=store,
            email=email,
            invitee=target_user,
            invited_by=invited_by,
            token_hash=token_hash,
            expires_at=expires_at,
        )

        inviter_name = invited_by.name or invited_by.email
        transaction.on_commit(
            lambda: send_store_invitation_email_task.delay(
                email, store.name, inviter_name, raw_token
            )
        )

        return invitation, raw_token

    @staticmethod
    @transaction.atomic
    def resend_invitation(invitation, invited_by):
        if invitation.status not in [InvitationStatus.PENDING, InvitationStatus.EXPIRED]:
            raise ValidationError("Only pending or expired invitations can be resent.")

        store = invitation.store
        if store.is_archived or store.status == StoreStatus.SUSPENDED:
            raise ValidationError("Cannot resend invitation for an inactive or archived store.")

        if StoreMembership.objects.filter(store=store, user__email__iexact=invitation.email).exists():
            invitation.status = InvitationStatus.REVOKED
            invitation.save(update_fields=["status"])
            raise ValidationError("This user is already a member of the store.")

        raw_token = secrets.token_urlsafe(32)
        invitation.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        invitation.expires_at = timezone.now() + timedelta(days=getattr(settings, "STORE_INVITATION_TTL_DAYS", 7))
        invitation.status = InvitationStatus.PENDING
        invitation.invited_by = invited_by
        invitation.responded_at = None
        invitation.save(update_fields=["token_hash", "expires_at", "status", "invited_by", "responded_at"])

        inviter_name = invited_by.name or invited_by.email
        transaction.on_commit(
            lambda: send_store_invitation_email_task.delay(
                invitation.email, store.name, inviter_name, raw_token
            )
        )

        return invitation, raw_token

    @staticmethod
    @transaction.atomic
    def revoke_invitation(invitation):
        if invitation.status != InvitationStatus.PENDING:
            raise ValidationError("Only pending invitations can be revoked.")

        invitation.status = InvitationStatus.REVOKED
        invitation.responded_at = timezone.now()
        invitation.save(update_fields=["status", "responded_at"])
        return invitation

    @staticmethod
    @transaction.atomic
    def accept_invitation(invitation, user):
        if not user.is_seller:
            raise ValidationError("Only registered sellers can accept store invitations.")

        if user.email.strip().lower() != invitation.email.strip().lower():
            raise ValidationError("This invitation was addressed to a different email address.")

        if invitation.is_expired:
            invitation.check_and_mark_expired()
            raise ValidationError("This invitation has expired.")

        if invitation.status != InvitationStatus.PENDING:
            raise ValidationError(f"This invitation is no longer valid (currently {invitation.status.lower()}).")

        store = StoreService._lock_store_for_membership_change(invitation.store)

        existing_membership = StoreMembership.objects.filter(store=store, user=user).first()
        if existing_membership:
            invitation.status = InvitationStatus.ACCEPTED
            invitation.responded_at = timezone.now()
            invitation.invitee = user
            invitation.save(update_fields=["status", "responded_at", "invitee"])
            return existing_membership

        membership = StoreMembership.objects.create(
            store=store,
            user=user,
            role=StoreRole.MANAGER,
            invitation=invitation,
        )

        invitation.status = InvitationStatus.ACCEPTED
        invitation.responded_at = timezone.now()
        invitation.invitee = user
        invitation.save(update_fields=["status", "responded_at", "invitee"])

        return membership

    @staticmethod
    @transaction.atomic
    def decline_invitation(invitation, user):
        if user.email.strip().lower() != invitation.email.strip().lower():
            raise ValidationError("This invitation was addressed to a different email address.")

        if invitation.status != InvitationStatus.PENDING:
            raise ValidationError("Only pending invitations can be declined.")

        invitation.status = InvitationStatus.DECLINED
        invitation.responded_at = timezone.now()
        invitation.invitee = user
        invitation.save(update_fields=["status", "responded_at", "invitee"])
        return invitation

    @staticmethod
    def accept_invitation_by_token(raw_token, user):
        token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
        invitation = (
            StoreInvitation.objects.select_related("store")
            .filter(token_hash=token_hash)
            .first()
        )
        if not invitation:
            raise ValidationError("Invalid or non-existent invitation token.")

        return StoreService.accept_invitation(invitation, user)

    # --- MEDIA (LOGO & BANNER) SERVICES ---

    @staticmethod
    def set_store_logo(store, image_file):
        if store.logo:
            store.logo.delete(save=False)
        store.logo = image_file
        store.save(update_fields=["logo", "updated_at"])
        return store

    @staticmethod
    def remove_store_logo(store):
        if store.logo:
            store.logo.delete(save=False)
        store.logo = None
        store.save(update_fields=["logo", "updated_at"])
        return store

    @staticmethod
    def set_store_banner(store, image_file):
        if store.banner:
            store.banner.delete(save=False)
        store.banner = image_file
        store.save(update_fields=["banner", "updated_at"])
        return store

    @staticmethod
    def remove_store_banner(store):
        if store.banner:
            store.banner.delete(save=False)
        store.banner = None
        store.save(update_fields=["banner", "updated_at"])
        return store
