from rest_framework.permissions import BasePermission

from .models import Store, StoreMembership


class IsSeller(BasePermission):
    message = "Only active sellers can access this resource."
    code = "NOT_A_SELLER"

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_seller)


class IsBusinessVerified(BasePermission):
    message = "Store business verification has not been completed or approved."
    code = "BUSINESS_VERIFICATION_REQUIRED"

    def has_permission(self, request, view):
        store_pk = view.kwargs.get("store_pk") or view.kwargs.get("pk")
        if not store_pk:
            return True
        store = Store.objects.filter(pk=store_pk).first()
        if not store:
            return True
        return store.is_business_verified


def get_store_membership(user, store_id):
    if not user or not user.is_authenticated:
        return None, None
    cache_attr = f"_store_membership_{store_id}"
    if hasattr(user, cache_attr):
        return getattr(user, cache_attr)
    membership = (
        StoreMembership.objects.select_related("store", "user")
        .filter(store_id=store_id, user=user)
        .first()
    )
    if not membership:
        result = (None, None)
    else:
        result = (membership.store, membership)
    setattr(user, cache_attr, result)
    return result
