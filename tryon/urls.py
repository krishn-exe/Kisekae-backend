from django.urls import path

from .views import (
    ComboDetailView,
    ComboItemAddView,
    ComboItemDeleteView,
    ComboListCreateView,
    SharedComboDetailView,
    SharedComboRedirectView,
    SharedComboSaveView,
    StoreProductARStatusDetailView,
    StoreProductsARStatusView,
    TryOnGenerateView,
    TryOnHistoryListView,
    TryOnRequestDetailView,
)

urlpatterns = [
    # Customer Virtual Try-On Generation & Status Polling
    path("generate/", TryOnGenerateView.as_view(), name="tryon-generate"),
    path("requests/<uuid:request_id>/", TryOnRequestDetailView.as_view(), name="tryon-request-detail"),
    path("history/", TryOnHistoryListView.as_view(), name="tryon-history-list"),

    # Combo CRUD
    path("combos/", ComboListCreateView.as_view(), name="combo-list-create"),
    path("combos/<int:pk>/", ComboDetailView.as_view(), name="combo-detail"),
    path("combos/<int:pk>/items/", ComboItemAddView.as_view(), name="combo-item-add"),
    path("combos/<int:pk>/items/<int:item_id>/", ComboItemDeleteView.as_view(), name="combo-item-delete"),

    # Public Combo Sharing & Deep Linking (Flutter live.kisekae.app / kisekae://)
    path("combos/share/<str:share_code>/", SharedComboDetailView.as_view(), name="shared-combo-detail"),
    path("combos/share/<str:share_code>/save/", SharedComboSaveView.as_view(), name="shared-combo-save"),
    path("combos/redirect/<str:share_code>/", SharedComboRedirectView.as_view(), name="shared-combo-redirect"),

    # Seller AR Status Endpoints
    path("stores/<int:store_pk>/tryon-status/", StoreProductsARStatusView.as_view(), name="store-products-ar-status"),
    path("stores/<int:store_pk>/products/<int:product_pk>/tryon-status/", StoreProductARStatusDetailView.as_view(), name="store-product-ar-status-detail"),
]
