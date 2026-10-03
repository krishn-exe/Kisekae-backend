import logging

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def success_response(
    data=None,
    message="Success",
    status_code=status.HTTP_200_OK,
    headers=None,
    tokens=None,
):
    payload = {
        "success": True,
        "message": message,
        "data": data,
    }

    if tokens:
        access_token = tokens.get("access")
        if access_token:
            if payload["data"] is None:
                payload["data"] = {}
            if isinstance(payload["data"], dict):
                payload["data"]["access"] = access_token

    response = Response(payload, status=status_code)

    if headers:
        for key, value in headers.items():
            response[key] = value

    if tokens:
        refresh_token = tokens.get("refresh")
        if refresh_token:
            response.set_cookie(
                key="refresh_token",
                value=str(refresh_token),
                httponly=True,
                samesite=getattr(settings, "AUTH_COOKIE_SAMESITE", "Lax"),
                secure=getattr(settings, "AUTH_COOKIE_SECURE", getattr(settings, "SESSION_COOKIE_SECURE", False)),
                path="/",
            )

    return response


def error_response(
    message="An error occurred",
    code="ERROR",
    details=None,
    status_code=status.HTTP_400_BAD_REQUEST,
    headers=None,
):
    payload = {
        "success": False,
        "message": message,
        "error": {
            "code": code,
            "details": details,
        },
    }
    response = Response(payload, status=status_code)

    if headers:
        for key, value in headers.items():
            response[key] = value

    return response


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        data = response.data
        error_code = "ERROR"
        details = None
        message = "An error occurred."

        if isinstance(data, dict):
            if "detail" in data:
                detail_val = data["detail"]
                message = str(detail_val)
                code_attr = getattr(detail_val, "code", None)
                if code_attr:
                    error_code = str(code_attr).upper()
                elif response.status_code == status.HTTP_401_UNAUTHORIZED:
                    error_code = "UNAUTHORIZED"
                elif response.status_code == status.HTTP_403_FORBIDDEN:
                    error_code = "PERMISSION_DENIED"
                elif response.status_code == status.HTTP_404_NOT_FOUND:
                    error_code = "NOT_FOUND"
                elif response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    error_code = "RATE_LIMIT_EXCEEDED"
            elif "non_field_errors" in data:
                err_list = data["non_field_errors"]
                if isinstance(err_list, list) and len(err_list) > 0:
                    message = str(err_list[0])
                    code_attr = getattr(err_list[0], "code", None)
                    error_code = str(code_attr).upper() if code_attr else "INVALID_CREDENTIALS"
                else:
                    message = str(err_list)
                    error_code = "INVALID_CREDENTIALS"
                other_fields = {k: v for k, v in data.items() if k != "non_field_errors"}
                details = other_fields if other_fields else None
            else:
                message = "Validation failed"
                error_code = "VALIDATION_ERROR"
                details = data
                if len(data) == 1:
                    field, errors = next(iter(data.items()))
                    if isinstance(errors, list) and len(errors) > 0:
                        message = str(errors[0])
        elif isinstance(data, list):
            if len(data) > 0:
                message = str(data[0])
                code_attr = getattr(data[0], "code", None)
                error_code = str(code_attr).upper() if code_attr else "VALIDATION_ERROR"
            else:
                message = "Validation failed"
                error_code = "VALIDATION_ERROR"
            details = data
        else:
            message = str(data)
        msg_lower = message.lower()
        if "inactive" in msg_lower:
            error_code = "ACCOUNT_INACTIVE"
        elif "not verified" in msg_lower:
            error_code = "EMAIL_NOT_VERIFIED"
        elif any(phrase in msg_lower for phrase in ["incorrect password", "no account found", "invalid credentials"]):
            error_code = "INVALID_CREDENTIALS"
        elif "token" in msg_lower and any(kw in msg_lower for kw in ["invalid", "expired", "blacklisted"]):
            error_code = "INVALID_TOKEN"

        response.data = {
            "success": False,
            "message": message,
            "error": {
                "code": error_code,
                "details": details,
            },
        }

    return response
