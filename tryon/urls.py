from django.urls import path

from .views import (
    ComboDetailView,
    ComboItemAddView,
    ComboItemDeleteView,
    ComboListCreateView,
)

urlpatterns = [
    # Combo CRUD
    path("combos/", ComboListCreateView.as_view(), name="combo-list-create"),
    path("combos/<int:pk>/", ComboDetailView.as_view(), name="combo-detail"),
    path("combos/<int:pk>/items/", ComboItemAddView.as_view(), name="combo-item-add"),
    path("combos/<int:pk>/items/<int:item_id>/", ComboItemDeleteView.as_view(), name="combo-item-delete"),
]
