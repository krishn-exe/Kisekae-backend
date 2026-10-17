from django.urls import path

from .views import (
    AddressDetailView,
    AddressListCreateView,
    AddressSetDefaultView,
    BecomeSellerView,
    ChangePasswordView,
    GitHubOAuthView,
    GoogleOAuthView,
    LoginPasswordView,
    LogoutView,
    OTPRequestView,
    OTPVerifyView,
    RegisterView,
    ResetPasswordView,
    TokenRefreshView,
    UserDetailView,
    UserSettingsView,
    WishlistDetailView,
    WishlistListCreateView,
    WishlistMoveToCartView,
)

urlpatterns = [
    # Authentication & Registration
    path("register/", RegisterView.as_view(), name="register"),
    path("login/password/", LoginPasswordView.as_view(), name="login-password"),
    path("oauth/google/", GoogleOAuthView.as_view(), name="google-oauth"),
    path("oauth/github/", GitHubOAuthView.as_view(), name="github-oauth"),
    path("otp/request/", OTPRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OTPVerifyView.as_view(), name="otp-verify"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", ChangePasswordView.as_view(), name="password-change"),
    path("password/reset/", ResetPasswordView.as_view(), name="password-reset"),
    path("become-seller/", BecomeSellerView.as_view(), name="become-seller"),

    # Customer Profile
    path("me/", UserDetailView.as_view(), name="me"),
    path("profile/", UserDetailView.as_view(), name="profile"),
    path("me/settings/", UserSettingsView.as_view(), name="me-settings"),

    # Customer Addresses
    path("addresses/", AddressListCreateView.as_view(), name="address-list-create"),
    path("addresses/<int:address_id>/", AddressDetailView.as_view(), name="address-detail"),
    path("addresses/<int:address_id>/default/", AddressSetDefaultView.as_view(), name="address-set-default"),

    # Customer Wishlist
    path("wishlist/", WishlistListCreateView.as_view(), name="wishlist-list-create"),
    path("wishlist/<int:product_id>/", WishlistDetailView.as_view(), name="wishlist-detail"),
    path("wishlist/<int:product_id>/move-to-cart/", WishlistMoveToCartView.as_view(), name="wishlist-move-to-cart"),
]