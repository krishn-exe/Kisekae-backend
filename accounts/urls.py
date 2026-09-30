from django.urls import path

from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ChangePasswordView,
    GoogleAuthView,
    LoginPasswordView,
    LogoutView,
    OTPRequestView,
    OTPVerifyView,
    RegisterView,
    ResetPasswordView,
    UserDetailView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/password/", LoginPasswordView.as_view(), name="login-password"),
    path("oauth/google/", GoogleAuthView.as_view(), name="google-auth"),
    path("otp/request/", OTPRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OTPVerifyView.as_view(), name="otp-verify"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", ChangePasswordView.as_view(), name="password-change"),
    path("password/reset/", ResetPasswordView.as_view(), name="password-reset"),
    path("me/", UserDetailView.as_view(), name="me"),
]