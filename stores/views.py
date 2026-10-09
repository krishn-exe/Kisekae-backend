from django.core.exceptions import ValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response

from .models import InvitationStatus, Store, StoreInvitation, StoreMembership, StoreRole, StoreStatus
from .pagination import StorePagination
from .permissions import IsSeller, get_store_membership
from .serializers import (
    AcceptTokenSerializer,
    AddMemberSerializer,
    CreateInvitationSerializer,
    ImageUploadSerializer,
    StoreCreateSerializer,
    StoreInvitationSerializer,
    StoreMemberSerializer,
    StoreSerializer,
    StoreUpdateSerializer,
    TransferOwnershipSerializer,
)
from .services import StoreService
from .validators import MAX_BANNER_SIZE, MAX_LOGO_SIZE, validate_store_image

STORE_ERROR_SCHEMA = inline_serializer(
    name="StoreErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="StoreErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)


class StoreMethodThrottleMixin:
    def get_throttles(self):
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        self.throttle_scope = "store_write" if self.request.method in write_methods else "store_read"
        return super().get_throttles()


class StoreListCreateView(StoreMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    pagination_class = StorePagination

    @extend_schema(
        tags=["Stores"],
        summary="List user's stores",
        description="Returns all stores the authenticated seller is a member of. By default excludes archived stores.",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by store status (ACTIVE, INACTIVE, SUSPENDED, ARCHIVED)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="role",
                description="Filter by role in store (OWNER, MANAGER)",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="include_archived",
                description="Set to 'true' to include archived stores",
                required=False,
                type=bool,
            ),
        ],
        responses={200: StoreSerializer(many=True), 401: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA},
    )
    def get(self, request):
        include_archived = request.query_params.get("include_archived", "").lower() == "true"
        status_filter = request.query_params.get("status")
        role_filter = request.query_params.get("role")

        if include_archived:
            queryset = Store.objects.filter(members=request.user)
        else:
            queryset = Store.objects.for_user(request.user)

        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        if role_filter:
            queryset = queryset.filter(memberships__user=request.user, memberships__role=role_filter.upper())

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = StoreSerializer(page, many=True, context={"request": request})

        return success_response(
            message="Stores fetched successfully",
            data={
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": serializer.data,
            },
        )

    @extend_schema(
        tags=["Stores"],
        summary="Create a new store",
        description="Creates a new store and designates the authenticated seller as the OWNER atomically.",
        request=StoreCreateSerializer,
        responses={201: StoreSerializer, 400: STORE_ERROR_SCHEMA, 401: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = StoreCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        store, _ = StoreService.create_store(
            user=request.user,
            **serializer.validated_data,
        )

        return success_response(
            message="Store created successfully.",
            data=StoreSerializer(store, context={"request": request}).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreDetailView(StoreMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="Get store details",
        description="Returns details of a specific store. User must be a member of the store.",
        responses={200: StoreSerializer, 404: STORE_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Store details fetched successfully.",
            data=StoreSerializer(store, context={"request": request}).data,
        )

    @extend_schema(
        tags=["Stores"],
        summary="Update store details",
        description="Updates details of a store (name, description, contact details, or active status). Managers and Owner can update.",
        request=StoreUpdateSerializer,
        responses={200: StoreSerializer, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def patch(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Archived store cannot be modified. Restore it first.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="This store is suspended and cannot be modified.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = StoreUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_store = StoreService.update_store(store, **serializer.validated_data)
        return success_response(
            message="Store updated successfully.",
            data=StoreSerializer(updated_store, context={"request": request}).data,
        )

    @extend_schema(
        tags=["Stores"],
        summary="Archive (soft-delete) store",
        description="Soft-archives a store. Only the store OWNER can archive the store. Revokes pending invitations.",
        responses={200: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if membership.role != StoreRole.OWNER:
            return error_response(
                message="Only the store owner can archive this store.",
                code="INSUFFICIENT_PERMISSIONS",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if store.is_archived:
            return error_response(
                message="Store is already archived.",
                code="ALREADY_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        StoreService.archive_store(store)
        return success_response(
            message="Store archived successfully.",
            data=None,
        )


class StoreRestoreView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="Restore an archived store",
        description="Restores a soft-archived store back to ACTIVE status. Only the store OWNER can restore.",
        request=None,
        responses={200: StoreSerializer, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if membership.role != StoreRole.OWNER:
            return error_response(
                message="Only the store owner can restore this store.",
                code="INSUFFICIENT_PERMISSIONS",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if not store.is_archived:
            return error_response(
                message="Store is not archived.",
                code="INVALID_STATUS",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        StoreService.restore_store(store)
        return success_response(
            message="Store restored successfully.",
            data=StoreSerializer(store, context={"request": request}).data,
        )


class StoreTransferOwnershipView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="Transfer store ownership",
        description="Transfers store ownership from current owner to an existing member. Only the current OWNER can perform this.",
        request=TransferOwnershipSerializer,
        responses={200: StoreSerializer, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if membership.role != StoreRole.OWNER:
            return error_response(
                message="Only the store owner can transfer ownership.",
                code="INSUFFICIENT_PERMISSIONS",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = TransferOwnershipSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            StoreService.transfer_ownership(
                store=store,
                current_owner=request.user,
                new_owner_member_id=serializer.validated_data["member_id"],
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="TRANSFER_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Store ownership transferred successfully.",
            data=StoreSerializer(store, context={"request": request}).data,
        )


class StoreMemberListCreateView(StoreMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="List store members",
        description="Lists all members of the store. Accessible by Owner and Managers.",
        responses={200: StoreMemberSerializer(many=True), 404: STORE_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        memberships = store.memberships.select_related("user").all()
        return success_response(
            message="Store members fetched successfully.",
            data=StoreMemberSerializer(memberships, many=True).data,
        )

    @extend_schema(
        tags=["Stores"],
        summary="Add a seller directly as a store manager",
        description="Adds an existing seller account as a manager to the store directly. Accessible by Owner and Managers.",
        request=AddMemberSerializer,
        responses={201: StoreMemberSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = AddMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_user = serializer.context["target_user"]

        try:
            new_membership = StoreService.add_member(
                store=store,
                user=target_user,
                role=StoreRole.MANAGER,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="ADD_MEMBER_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Member added successfully.",
            data=StoreMemberSerializer(new_membership).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreMemberDeleteView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="Remove a member from the store",
        description="Removes a manager from the store. Cannot remove the owner or yourself (use leave instead).",
        responses={200: STORE_ERROR_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk, member_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            StoreService.remove_member(
                store=store,
                member_id=member_id,
                actor_user=request.user,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="REMOVE_MEMBER_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Member removed successfully.",
            data=None,
        )


class StoreLeaveView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Stores"],
        summary="Leave a store",
        description="Allows a manager to leave the store. The store owner cannot leave without transferring ownership first.",
        request=None,
        responses={200: STORE_ERROR_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            StoreService.leave_store(store=store, user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="LEAVE_STORE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="You have successfully left the store.",
            data=None,
        )


# --- INVITATION VIEWS ---


class StoreInvitationListCreateView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="store_invitations_list",
        tags=["Store Invitations"],
        summary="List invitations for a store",
        description="Lists all sent invitations for this store. Filter by ?status= (PENDING, ACCEPTED, DECLINED, REVOKED, EXPIRED).",
        parameters=[
            OpenApiParameter(
                name="status",
                description="Filter by invitation status",
                required=False,
                type=str,
            ),
        ],
        responses={200: StoreInvitationSerializer(many=True), 404: STORE_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        queryset = store.invitations.select_related("store", "invited_by").all()
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        return success_response(
            message="Invitations fetched successfully.",
            data=StoreInvitationSerializer(queryset, many=True).data,
        )

    @extend_schema(
        tags=["Store Invitations"],
        summary="Invite a seller to manage the store",
        description="Sends an email invitation to manage this store as a Manager. Can be initiated by Owner or Managers.",
        request=CreateInvitationSerializer,
        responses={201: StoreInvitationSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = CreateInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        try:
            invitation, _ = StoreService.create_invitation(
                store=store,
                email=email,
                invited_by=request.user,
            )
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="INVITATION_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message=f"Invitation sent to {email}.",
            data=StoreInvitationSerializer(invitation).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreInvitationRevokeView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Store Invitations"],
        summary="Revoke an invitation",
        description="Revokes a pending invitation for this store.",
        responses={200: STORE_ERROR_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk, inv_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        invitation = store.invitations.filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found in this store.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            StoreService.revoke_invitation(invitation)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="REVOKE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Invitation revoked successfully.",
            data=None,
        )


class StoreInvitationResendView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Store Invitations"],
        summary="Resend an invitation",
        description="Generates a new token and resets the 7-day expiration for a pending or expired invitation.",
        request=None,
        responses={200: StoreInvitationSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk, inv_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        invitation = store.invitations.filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found in this store.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            invitation, _ = StoreService.resend_invitation(invitation, invited_by=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="RESEND_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Invitation resent successfully.",
            data=StoreInvitationSerializer(invitation).data,
        )


class InviteeInvitationListView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        operation_id="seller_pending_invitations_list",
        tags=["Seller Invitations"],
        summary="List my pending store invitations",
        description="Lists all pending invitations addressed to the authenticated seller's email address.",
        responses={200: StoreInvitationSerializer(many=True)},
    )
    def get(self, request):
        user_email = (getattr(request.user, "email", None) or "").strip().lower()
        if not user_email:
            return success_response(
                message="Pending invitations fetched successfully.",
                data=[],
            )

        invitations = (
            StoreInvitation.objects.filter(
                email__iexact=user_email,
                status=InvitationStatus.PENDING,
            )
            .select_related("store", "invited_by")
            .all()
        )
        return success_response(
            message="Pending invitations fetched successfully.",
            data=StoreInvitationSerializer(invitations, many=True).data,
        )


class InviteeInvitationAcceptView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Seller Invitations"],
        summary="Accept a store invitation",
        description="Accepts an invitation addressed to the authenticated seller's email and grants Manager membership.",
        request=None,
        responses={200: StoreMemberSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, inv_id):
        invitation = StoreInvitation.objects.select_related("store").filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            membership = StoreService.accept_invitation(invitation, user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="ACCEPT_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Invitation accepted successfully. You are now a manager of this store.",
            data=StoreMemberSerializer(membership).data,
        )


class InviteeInvitationDeclineView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Seller Invitations"],
        summary="Decline a store invitation",
        description="Declines an invitation addressed to the authenticated seller's email.",
        request=None,
        responses={200: STORE_ERROR_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, inv_id):
        invitation = StoreInvitation.objects.select_related("store").filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        try:
            StoreService.decline_invitation(invitation, user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="DECLINE_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Invitation declined.",
            data=None,
        )


class InviteeInvitationTokenAcceptView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]

    @extend_schema(
        tags=["Seller Invitations"],
        summary="Accept an invitation using secret token",
        description="Accepts an invitation using the secret token received in the invitation email or deep link.",
        request=AcceptTokenSerializer,
        responses={200: StoreMemberSerializer, 400: STORE_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = AcceptTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["token"]

        try:
            membership = StoreService.accept_invitation_by_token(raw_token, user=request.user)
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="ACCEPT_FAILED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return success_response(
            message="Invitation accepted successfully. You are now a manager of this store.",
            data=StoreMemberSerializer(membership).data,
        )


# --- MEDIA (LOGO & BANNER) VIEWS ---


class StoreLogoView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Stores"],
        summary="Upload store logo",
        description="Uploads or replaces the store logo (JPEG, PNG, WEBP; max 2MB). Accessible by Owner and Managers.",
        request=ImageUploadSerializer,
        responses={200: StoreSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def put(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image_file = serializer.validated_data["file"]

        try:
            validate_store_image(image_file, MAX_LOGO_SIZE, "logo")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="INVALID_IMAGE",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        updated_store = StoreService.set_store_logo(store, image_file)
        return success_response(
            message="Store logo uploaded successfully.",
            data=StoreSerializer(updated_store, context={"request": request}).data,
        )

    @extend_schema(
        tags=["Stores"],
        summary="Remove store logo",
        description="Removes the store logo. Accessible by Owner and Managers.",
        request=None,
        responses={200: StoreSerializer, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        updated_store = StoreService.remove_store_logo(store)
        return success_response(
            message="Store logo removed successfully.",
            data=StoreSerializer(updated_store, context={"request": request}).data,
        )


class StoreBannerView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Stores"],
        summary="Upload store banner",
        description="Uploads or replaces the store banner (JPEG, PNG, WEBP; max 5MB). Accessible by Owner and Managers.",
        request=ImageUploadSerializer,
        responses={200: StoreSerializer, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def put(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ImageUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image_file = serializer.validated_data["file"]

        try:
            validate_store_image(image_file, MAX_BANNER_SIZE, "banner")
        except ValidationError as e:
            msg = e.messages[0] if hasattr(e, "messages") and e.messages else str(e)
            return error_response(
                message=msg,
                code="INVALID_IMAGE",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        updated_store = StoreService.set_store_banner(store, image_file)
        return success_response(
            message="Store banner uploaded successfully.",
            data=StoreSerializer(updated_store, context={"request": request}).data,
        )

    @extend_schema(
        tags=["Stores"],
        summary="Remove store banner",
        description="Removes the store banner. Accessible by Owner and Managers.",
        request=None,
        responses={200: StoreSerializer, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        updated_store = StoreService.remove_store_banner(store)
        return success_response(
            message="Store banner removed successfully.",
            data=StoreSerializer(updated_store, context={"request": request}).data,
        )
