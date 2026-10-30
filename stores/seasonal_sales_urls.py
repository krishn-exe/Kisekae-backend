from django.urls import path

from .seasonal_sales_views import (
    StoreEnrollSeasonalSaleProductsView,
    StoreSeasonalSaleDetailView,
    StoreSeasonalSaleListView,
    StoreSeasonalSaleProductItemView,
)

urlpatterns = [
    # List seasonal sales for store & participation status
    path("", StoreSeasonalSaleListView.as_view(), name="store-seasonal-sales-list"),

    # Seasonal sale details & enrolled products for store
    path("<int:sale_id>/", StoreSeasonalSaleDetailView.as_view(), name="store-seasonal-sale-detail"),

    # Enroll store products in seasonal sale (max 5 per store)
    path("<int:sale_id>/products/", StoreEnrollSeasonalSaleProductsView.as_view(), name="store-enroll-seasonal-sale-products"),

    # Manage / unenroll individual product in seasonal sale
    path("<int:sale_id>/products/<int:product_id>/", StoreSeasonalSaleProductItemView.as_view(), name="store-seasonal-sale-product-item"),
]
