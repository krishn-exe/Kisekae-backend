from django.urls import include, path

from .views import (
    BusinessVerificationView,
    InviteeInvitationAcceptView,
    InviteeInvitationDeclineView,
    InviteeInvitationListView,
    InviteeInvitationTokenAcceptView,
    StoreDetailView,
    StoreInvitationListCreateView,
    StoreInvitationRedirectView,
    StoreInvitationResendView,
    StoreInvitationRevokeView,
    StoreLeaveView,
    StoreListCreateView,
    StoreMemberDeleteView,
    StoreMemberListCreateView,
    StoreRestoreView,
    StoreTransferOwnershipView,
)

urlpatterns = [
    # Store List & Create
    path("", StoreListCreateView.as_view(), name="store-list-create"),

    # Invitee-side Invitation Management (must precede <int:pk>/)
    path("invitations/", InviteeInvitationListView.as_view(), name="invitee-invitation-list"),
    path("invitations/redirect/", StoreInvitationRedirectView.as_view(), name="store-invitation-redirect"),
    path("invitations/accept-token/", InviteeInvitationTokenAcceptView.as_view(), name="invitee-invitation-token-accept"),
    path("invitations/<int:inv_id>/accept/", InviteeInvitationAcceptView.as_view(), name="invitee-invitation-accept"),
    path("invitations/<int:inv_id>/decline/", InviteeInvitationDeclineView.as_view(), name="invitee-invitation-decline"),

    # Store Detail, Restore, Transfer, Verification
    path("<int:pk>/", StoreDetailView.as_view(), name="store-detail"),
    path("<int:pk>/verification/", BusinessVerificationView.as_view(), name="store-verification"),
    path("<int:pk>/restore/", StoreRestoreView.as_view(), name="store-restore"),
    path("<int:pk>/transfer-ownership/", StoreTransferOwnershipView.as_view(), name="store-transfer-ownership"),

    # Store Members
    path("<int:pk>/members/", StoreMemberListCreateView.as_view(), name="store-member-list-create"),
    path("<int:pk>/members/<int:member_id>/", StoreMemberDeleteView.as_view(), name="store-member-delete"),
    path("<int:pk>/leave/", StoreLeaveView.as_view(), name="store-leave"),

    # Store-side Invitations
    path("<int:pk>/invitations/", StoreInvitationListCreateView.as_view(), name="store-invitation-list-create"),
    path("<int:pk>/invitations/<int:inv_id>/", StoreInvitationRevokeView.as_view(), name="store-invitation-revoke"),
    path("<int:pk>/invitations/<int:inv_id>/resend/", StoreInvitationResendView.as_view(), name="store-invitation-resend"),

    # Store Products
    path("<int:pk>/products/", include("products.urls")),

    # Store Inventory & Stock Management
    path("<int:pk>/", include("inventory.urls")),

    # Store Orders Management
    path("<int:pk>/", include("orders.urls")),

    # Store Analytics
    path("<int:pk>/analytics/", include("analytics.urls")),
]
