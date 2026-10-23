from django.urls import path

from .views import (
    ComboDetailView,
    ComboItemAddView,
    ComboItemDeleteView,
    ComboListCreateView,
    StoreProductARStatusDetailView,
    StoreProductsARStatusView,
)

urlpatterns = [
    # Combo CRUD
    path("combos/", ComboListCreateView.as_view(), name="combo-list-create"),
    path("combos/<int:pk>/", ComboDetailView.as_view(), name="combo-detail"),
    path("combos/<int:pk>/items/", ComboItemAddView.as_view(), name="combo-item-add"),
    path("combos/<int:pk>/items/<int:item_id>/", ComboItemDeleteView.as_view(), name="combo-item-delete"),

    # Seller AR Status Endpoints
    path("stores/<int:store_pk>/tryon-status/", StoreProductsARStatusView.as_view(), name="store-products-ar-status"),
    path("stores/<int:store_pk>/products/<int:product_pk>/tryon-status/", StoreProductARStatusDetailView.as_view(), name="store-product-ar-status-detail"),
]
