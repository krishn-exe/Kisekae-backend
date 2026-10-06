from django.http import HttpResponse
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

def google_to_app(request):
    qs = request.META.get("QUERY_STRING", "")
    return HttpResponse(
        status=302,
        headers={"Location": f"kisekae://auth/google/callback?{qs}"},
    )

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('stores/', include('stores.urls')),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),

    path("googleAuth/redirect", google_to_app),
]
