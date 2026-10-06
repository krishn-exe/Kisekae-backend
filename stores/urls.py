from django.urls import path

from .views import (
    StoreDetailView,
    StoreLeaveView,
    StoreListCreateView,
    StoreMemberDeleteView,
    StoreMemberListCreateView,
    StoreRestoreView,
    StoreTransferOwnershipView,
)

urlpatterns = [
    path("", StoreListCreateView.as_view(), name="store-list-create"),
    path("<int:pk>/", StoreDetailView.as_view(), name="store-detail"),
    path("<int:pk>/restore/", StoreRestoreView.as_view(), name="store-restore"),
    path("<int:pk>/transfer-ownership/", StoreTransferOwnershipView.as_view(), name="store-transfer-ownership"),
    path("<int:pk>/members/", StoreMemberListCreateView.as_view(), name="store-member-list-create"),
    path("<int:pk>/members/<int:member_id>/", StoreMemberDeleteView.as_view(), name="store-member-delete"),
    path("<int:pk>/leave/", StoreLeaveView.as_view(), name="store-leave"),
]
