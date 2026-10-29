from django.urls import path

from accounts.views import (
    AddressDetailView,
    AddressListCreateView,
    AddressSetDefaultView,
    UserDetailView,
    UserSettingsView,
    WishlistDetailView,
    WishlistListCreateView,
    WishlistBulkMoveToCartView,
    WishlistMoveToCartView,
)
from accounts.wallet_views import (
    WalletAddFundsVerifyView,
    WalletAddFundsView,
    WalletDetailView,
    WalletTransactionListView,
)
from cart.views import (
    AddToCartView,
    CartClearView,
    CartDetailView,
    CartItemDetailView,
    CartValidateView,
)
from notifications.views import CustomerDeviceView
from tryon.views import (
    ComboDetailView,
    ComboItemAddView,
    ComboItemDeleteView,
    ComboListCreateView,
    SharedComboDetailView,
    SharedComboRedirectView,
    SharedComboSaveView,
)

urlpatterns = [
    # Personal Information & Account Management
    path("me/", UserDetailView.as_view(), name="profile-me"),
    path("me/settings/", UserSettingsView.as_view(), name="profile-settings"),
    path("devices/", CustomerDeviceView.as_view(), name="profile-devices"),

    # Customer Delivery Addresses
    path("address/", AddressListCreateView.as_view(), name="profile-address-list-create"),
    path("address/<int:address_id>/", AddressDetailView.as_view(), name="profile-address-detail"),
    path("address/<int:address_id>/default/", AddressSetDefaultView.as_view(), name="profile-address-set-default"),

    # Customer Wallet
    path("wallet/", WalletDetailView.as_view(), name="profile-wallet-detail"),
    path("wallet/add-funds/", WalletAddFundsView.as_view(), name="profile-wallet-add-funds"),
    path("wallet/add-funds/verify/", WalletAddFundsVerifyView.as_view(), name="profile-wallet-add-funds-verify"),
    path("wallet/transactions/", WalletTransactionListView.as_view(), name="profile-wallet-transactions"),

    # Customer Wishlist
    path("wishlist/", WishlistListCreateView.as_view(), name="profile-wishlist-list-create"),
    path("wishlist/<int:product_id>/", WishlistDetailView.as_view(), name="profile-wishlist-detail"),
    path("wishlist/move-to-cart/", WishlistBulkMoveToCartView.as_view(), name="wishlist-bulk-move-to-cart"),
    path("wishlist/<int:product_id>/move-to-cart/", WishlistMoveToCartView.as_view(), name="wishlist-move-to-cart"),

    # Customer Shopping Cart
    path("cart/", CartDetailView.as_view(), name="profile-cart-detail"),
    path("cart/clear/", CartClearView.as_view(), name="profile-cart-clear"),
    path("cart/items/", AddToCartView.as_view(), name="profile-cart-add-item"),
    path("cart/items/<int:item_id>/", CartItemDetailView.as_view(), name="profile-cart-item-detail"),
    path("cart/validate/", CartValidateView.as_view(), name="profile-cart-validate"),

    # Customer Saved AR Looks / Combos
    path("combos/", ComboListCreateView.as_view(), name="combo-list-create"),
    path("combos/<int:pk>/", ComboDetailView.as_view(), name="combo-detail"),
    path("combos/<int:pk>/items/", ComboItemAddView.as_view(), name="combo-item-add"),
    path("combos/<int:pk>/items/<int:item_id>/", ComboItemDeleteView.as_view(), name="combo-item-delete"),
    path("combos/share/<str:share_code>/", SharedComboDetailView.as_view(), name="shared-combo-detail"),
    path("combos/share/<str:share_code>/save/", SharedComboSaveView.as_view(), name="shared-combo-save"),
    path("combos/redirect/<str:share_code>/", SharedComboRedirectView.as_view(), name="shared-combo-redirect"),
]
