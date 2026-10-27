from django.urls import path

from .views import (
    TryOnGenerateView,
    TryOnHistoryListView,
    TryOnRequestDetailView,
)

urlpatterns = [
    # Customer Virtual Try-On Generation & Status Polling
    path("generate/", TryOnGenerateView.as_view(), name="tryon-generate"),
    path("requests/<uuid:request_id>/", TryOnRequestDetailView.as_view(), name="tryon-request-detail"),
    path("history/", TryOnHistoryListView.as_view(), name="tryon-history-list"),
]
