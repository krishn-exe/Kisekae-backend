from rest_framework.permissions import BasePermission

from .models import StoreMembership, StoreRole


class IsSeller(BasePermission):
    message = "Only active sellers can access this resource."
    code = "NOT_A_SELLER"

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_seller)


def get_store_membership(user, store_id):
    membership = (
        StoreMembership.objects.select_related("store", "user")
        .filter(store_id=store_id, user=user)
        .first()
    )
    if not membership:
        return None, None
    return membership.store, membership
