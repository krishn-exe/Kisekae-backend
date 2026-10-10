from django.http import HttpResponse, HttpResponseForbidden
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
import os
from django_prometheus import exports

def secured_metrics_view(request):
    token = os.getenv("PROMETHEUS_METRICS_TOKEN")
    if token:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header == f"Bearer {token}":
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
    path('metrics', secured_metrics_view, name='prometheus-django-metrics'),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('stores/', include('stores.urls')),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    path("googleAuth/redirect", google_to_app),
]
