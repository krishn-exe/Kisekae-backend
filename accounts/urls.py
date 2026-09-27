from django.urls import path

from .views import LoginPasswordView, OTPRequestView, OTPVerifyView, RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/password/", LoginPasswordView.as_view(), name="login-password"),
    path("otp/request/", OTPRequestView.as_view(), name="otp-request"),
    path("otp/verify/", OTPVerifyView.as_view(), name="otp-verify"),
]