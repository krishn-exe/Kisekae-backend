from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Store, StoreMembership, StoreRole, StoreStatus


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
    def archive_store(store):
        store.archive()
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
