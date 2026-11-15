import hmac
import os
from django.contrib import admin
from django.http import HttpResponse, HttpResponseForbidden
from django.urls import include, path, re_path
from django_prometheus import exports
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

def secured_metrics_view(request):
    token = os.getenv("PROMETHEUS_METRICS_TOKEN", "").strip()
    if token:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        expected_header = f"Bearer {token}"
        if hmac.compare_digest(auth_header, expected_header):
            return exports.ExportToDjangoView(request)
        return HttpResponseForbidden("Invalid or missing token.")
    
    if request.user.is_authenticated and request.user.is_staff:
        return exports.ExportToDjangoView(request)
        
    return HttpResponseForbidden("Metrics are secured. Please configure PROMETHEUS_METRICS_TOKEN or login as a staff member.")

def google_to_app(request):
    qs = request.META.get("QUERY_STRING", "")
    return HttpResponse(
        status=302,
        headers={"Location": f"kisekae://auth/google/callback?{qs}"},
    )

urlpatterns = [
    re_path(r'^metrics/?$', secured_metrics_view, name='prometheus-django-metrics'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('profile/', include('accounts.profile_urls')),
    path('stores/', include('stores.urls')),
    path('products/', include('products.public_urls')),
    path('orders/', include('orders.customer_urls')),
    path('reviews/', include('reviews.urls')),
    path('tryon/', include('tryon.urls')),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    path("googleAuth/redirect", google_to_app),
]
