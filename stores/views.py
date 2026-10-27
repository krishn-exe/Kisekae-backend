from django.db.models import Count
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response

from .models import (
    BusinessVerification,
    InvitationStatus,
    Store,
    StoreInvitation,
    StoreMembership,
    StoreRole,
    StoreStatus,
    VerificationStatus,
)
from .pagination import StorePagination
from .permissions import IsSeller, get_store_membership
from .serializers import (
    AcceptTokenSerializer,
    AddMemberSerializer,
    BusinessVerificationSerializer,
    BusinessVerificationSubmitSerializer,
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

from KiseKaeDjango.schema.helpers import api_response, ERROR_SCHEMA

STORE_ERROR_SCHEMA = ERROR_SCHEMA
STORE_ACTION_SUCCESS_SCHEMA = api_response(serializers.JSONField(default=None, allow_null=True), "StoreActionSuccessResponse")
STORE_DETAIL_RESPONSE_SCHEMA = api_response(StoreSerializer, "StoreDetailResponse")
STORE_LIST_RESPONSE_SCHEMA = api_response(StoreSerializer, "StoreListResponse", many=True)
STORE_MEMBER_DETAIL_RESPONSE_SCHEMA = api_response(StoreMemberSerializer, "StoreMemberDetailResponse")
STORE_MEMBER_LIST_RESPONSE_SCHEMA = api_response(StoreMemberSerializer, "StoreMemberListResponse", many=True)
STORE_INVITATION_DETAIL_RESPONSE_SCHEMA = api_response(StoreInvitationSerializer, "StoreInvitationDetailResponse")
STORE_INVITATION_LIST_RESPONSE_SCHEMA = api_response(StoreInvitationSerializer, "StoreInvitationListResponse", many=True)
BUSINESS_VERIFICATION_RESPONSE_SCHEMA = api_response(BusinessVerificationSerializer, "BusinessVerificationResponse")


class StoreMethodThrottleMixin:
    def get_throttles(self):
        write_methods = {"POST", "PUT", "PATCH", "DELETE"}
        self.throttle_scope = "store_write" if self.request.method in write_methods else "store_read"
        return super().get_throttles()


class StoreListCreateView(StoreMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    pagination_class = StorePagination

    @extend_schema(
        operation_id="seller_store_list",
        tags=["Store - Management"],
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
        responses={200: STORE_LIST_RESPONSE_SCHEMA, 401: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA},
    )
    def get(self, request):
        include_archived = request.query_params.get("include_archived", "").lower() == "true"
        status_filter = request.query_params.get("status")
        role_filter = request.query_params.get("role")

        user_memberships = StoreMembership.objects.filter(user=request.user)
        if role_filter:
            user_memberships = user_memberships.filter(role=role_filter.upper())
        user_store_ids = user_memberships.values("store_id")

        if include_archived:
            queryset = Store.objects.filter(id__in=user_store_ids)
        else:
            queryset = Store.objects.not_archived().filter(id__in=user_store_ids)

        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        queryset = (
            queryset.annotate(member_count=Count("memberships", distinct=True))
            .select_related("business_verification")
            .prefetch_related("memberships")
            .order_by("-created_at")
        )

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
        operation_id="seller_store_create",
        tags=["Store - Management"],
        summary="Create a new store",
        description="Creates a new store with required name, description, logo, and banner, plus optional website and instagram links. Designates the authenticated seller as the OWNER atomically.",
        request=StoreCreateSerializer,
        responses={201: STORE_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 401: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA},
        examples=[
            OpenApiExample(
                "Store Create Form Data",
                value={
                    "name": "Urban Edge Store",
                    "description": "High-fashion minimalist streetwear and essentials.",
                    "website_link": "https://urbanedge.in",
                    "instagram_link": "@urbanedgestore",
                    "contact_email": "contact@urbanedge.in",
                    "contact_phone": "+919876543210",
                },
                request_only=True,
            ),
        ],
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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        operation_id="seller_store_detail",
        tags=["Store - Management"],
        summary="Get store details",
        description="Returns details of a specific store. User must be a member of the store.",
        responses={200: STORE_DETAIL_RESPONSE_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
        operation_id="seller_store_replace",
        tags=["Store - Management"],
        summary="Replace store details",
        description="Replaces store details, logo, banner, and social links. Managers and Owner can update.",
        request=StoreUpdateSerializer,
        responses={200: STORE_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
        examples=[
            OpenApiExample(
                "Store Replace Example",
                value={
                    "name": "Kisekae Boutique Flagship",
                    "description": "Premium apparel and contemporary street style collection.",
                    "website_link": "https://kisekae-boutique.com",
                    "instagram_link": "@kisekae.boutique",
                    "contact_email": "support@kisekae-boutique.com",
                    "contact_phone": "+919876543210",
                    "status": "ACTIVE",
                },
                request_only=True,
            ),
        ],
    )
    def put(self, request, pk):
        return self.patch(request, pk)

    @extend_schema(
        operation_id="seller_store_update",
        tags=["Store - Management"],
        summary="Update store details",
        description="Updates details of a store (name, description, contact details, or active status). Managers and Owner can update.",
        request=StoreUpdateSerializer,
        responses={200: STORE_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
        tags=["Store - Management"],
        summary="Archive (soft-delete) store",
        description="Soft-archives a store. Only the store OWNER can archive the store. Revokes pending invitations.",
        responses={200: STORE_ACTION_SUCCESS_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Management"],
        summary="Restore an archived store",
        description="Restores a soft-archived store back to ACTIVE status. Only the store OWNER can restore.",
        request=None,
        responses={200: STORE_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Management"],
        summary="Transfer store ownership",
        description="Transfers store ownership from current owner to an existing member. Only the current OWNER can perform this.",
        request=TransferOwnershipSerializer,
        responses={200: STORE_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 403: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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

        StoreService.transfer_ownership(
            store=store,
            current_owner=request.user,
            new_owner_member_id=serializer.validated_data["member_id"],
        )

        return success_response(
            message="Store ownership transferred successfully.",
            data=StoreSerializer(store, context={"request": request}).data,
        )


class StoreMemberListCreateView(StoreMethodThrottleMixin, APIView):
    throttle_scope = "store_read"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Management"],
        summary="List store members",
        description="Lists all members of the store. Accessible by Owner and Managers.",
        responses={200: STORE_MEMBER_LIST_RESPONSE_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
        tags=["Store - Management"],
        summary="Add a seller directly as a store manager",
        description="Adds an existing seller account as a manager to the store directly. Accessible by Owner and Managers.",
        request=AddMemberSerializer,
        responses={201: STORE_MEMBER_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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

        new_membership = StoreService.add_member(
            store=store,
            user=target_user,
            role=StoreRole.MANAGER,
        )

        return success_response(
            message="Member added successfully.",
            data=StoreMemberSerializer(new_membership).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreMemberDeleteView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Management"],
        summary="Remove a member from the store",
        description="Removes a manager from the store. Cannot remove the owner or yourself (use leave instead).",
        responses={200: STORE_ACTION_SUCCESS_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def delete(self, request, pk, member_id):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        StoreService.remove_member(
            store=store,
            member_id=member_id,
            actor_user=request.user,
        )

        return success_response(
            message="Member removed successfully.",
            data=None,
        )


class StoreLeaveView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Management"],
        summary="Leave a store",
        description="Allows a manager to leave the store. The store owner cannot leave without transferring ownership first.",
        request=None,
        responses={200: STORE_ACTION_SUCCESS_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        StoreService.leave_store(store=store, user=request.user)

        return success_response(
            message="You have successfully left the store.",
            data=None,
        )


# --- INVITATION VIEWS ---


class StoreInvitationListCreateView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        operation_id="store_invitations_list",
        tags=["Store - Invitations"],
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
        responses={200: STORE_INVITATION_LIST_RESPONSE_SCHEMA, 404: STORE_ERROR_SCHEMA},
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
        tags=["Store - Invitations"],
        summary="Invite a seller to manage the store",
        description="Sends an email invitation to manage this store as a Manager. Can be initiated by Owner or Managers.",
        request=CreateInvitationSerializer,
        responses={201: STORE_INVITATION_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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

        invitation, _ = StoreService.create_invitation(
            store=store,
            email=email,
            invited_by=request.user,
        )

        return success_response(
            message=f"Invitation sent to {email}.",
            data=StoreInvitationSerializer(invitation).data,
            status_code=status.HTTP_201_CREATED,
        )


class StoreInvitationRevokeView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Invitations"],
        summary="Revoke an invitation",
        description="Revokes a pending invitation for this store.",
        responses={200: STORE_ACTION_SUCCESS_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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

        StoreService.revoke_invitation(invitation)

        return success_response(
            message="Invitation revoked successfully.",
            data=None,
        )


class StoreInvitationResendView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Invitations"],
        summary="Resend an invitation",
        description="Generates a new token and resets the 7-day expiration for a pending or expired invitation.",
        request=None,
        responses={200: STORE_INVITATION_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
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

        invitation, _ = StoreService.resend_invitation(invitation, invited_by=request.user)

        return success_response(
            message="Invitation resent successfully.",
            data=StoreInvitationSerializer(invitation).data,
        )


class InviteeInvitationListView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        operation_id="seller_pending_invitations_list",
        tags=["Store - Seller Invitations"],
        summary="List my pending store invitations",
        description="Lists all pending invitations addressed to the authenticated seller's email address.",
        responses={200: STORE_INVITATION_LIST_RESPONSE_SCHEMA},
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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Seller Invitations"],
        summary="Accept a store invitation",
        description="Accepts an invitation addressed to the authenticated seller's email and grants Manager membership.",
        request=None,
        responses={200: STORE_MEMBER_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, inv_id):
        invitation = StoreInvitation.objects.select_related("store").filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        membership = StoreService.accept_invitation(invitation, user=request.user)

        return success_response(
            message="Invitation accepted successfully. You are now a manager of this store.",
            data=StoreMemberSerializer(membership).data,
        )


class InviteeInvitationDeclineView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Seller Invitations"],
        summary="Decline a store invitation",
        description="Declines an invitation addressed to the authenticated seller's email.",
        request=None,
        responses={200: STORE_ACTION_SUCCESS_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, inv_id):
        invitation = StoreInvitation.objects.select_related("store").filter(id=inv_id).first()
        if not invitation:
            return error_response(
                message="Invitation not found.",
                code="INVITATION_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        StoreService.decline_invitation(invitation, user=request.user)

        return success_response(
            message="Invitation declined.",
            data=None,
        )


class InviteeInvitationTokenAcceptView(APIView):
    throttle_scope = "store_invite"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Seller Invitations"],
        summary="Accept an invitation using secret token",
        description="Accepts an invitation using the secret token received in the invitation email or deep link.",
        request=AcceptTokenSerializer,
        responses={200: STORE_MEMBER_DETAIL_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA},
    )
    def post(self, request):
        serializer = AcceptTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = serializer.validated_data["token"]

        membership = StoreService.accept_invitation_by_token(raw_token, user=request.user)

        return success_response(
            message="Invitation accepted successfully. You are now a manager of this store.",
            data=StoreMemberSerializer(membership).data,
        )


# --- INVITATION REDIRECT VIEW ---


class StoreInvitationRedirectView(APIView):
    permission_classes = []
    throttle_classes = []

    @extend_schema(
        operation_id="seller_invitation_email_redirect",
        tags=["Store - Seller Invitations"],
        summary="Invitation email deep-link redirect",
        description="Redirects invitation email links into the KiseKae Seller app with fallback HTML token presentation.",
        parameters=[
            OpenApiParameter(name="token", description="Raw invitation token", required=True, type=str),
        ],
        responses={200: OpenApiResponse(description="HTML redirect page opening kisekae-seller:// with fallback token display")},
    )
    def get(self, request):
        token = request.query_params.get("token", "").strip()
        app_url = f"kisekae-seller://invitations/accept?token={token}"
        html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Accept Store Invitation - KiseKae Seller</title>
    <meta http-equiv="refresh" content="0;url={app_url}">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #faf8f5; color: #2d2527; }}
        .card {{ background: white; padding: 32px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); max-width: 420px; text-align: center; }}
        .btn {{ display: inline-block; background: #802B3E; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 16px; }}
        .token {{ font-family: monospace; background: #f3f0ec; padding: 8px 12px; border-radius: 6px; font-weight: bold; margin: 12px 0; word-break: break-all; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Opening KiseKae Seller...</h2>
        <p>Redirecting to accept your store invitation.</p>
        <a href="{app_url}" class="btn">Open App</a>
        <p style="margin-top: 24px; font-size: 13px; color: #78716c;">If the app does not open automatically, copy this invitation token and enter it in the app:</p>
        <div class="token">{token}</div>
    </div>
</body>
</html>"""
        return HttpResponse(html_content, content_type="text/html")


# --- BUSINESS VERIFICATION VIEWS ---


class BusinessVerificationView(APIView):
    throttle_scope = "store_write"
    permission_classes = [IsAuthenticated, IsSeller]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @extend_schema(
        tags=["Store - Verification"],
        summary="Get business verification details",
        description="Returns the business verification status and submitted details for the store.",
        responses={200: BUSINESS_VERIFICATION_RESPONSE_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def get(self, request, pk):
        store, _ = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        verification = getattr(store, "business_verification", None)
        if not verification:
            return success_response(
                message="No business verification submitted yet.",
                data={
                    "is_submitted": False,
                    "is_business_verified": False,
                    "verification": None,
                },
            )

        return success_response(
            message="Business verification fetched successfully.",
            data={
                "is_submitted": True,
                "is_business_verified": store.is_business_verified,
                "verification": BusinessVerificationSerializer(verification).data,
            },
        )

    @extend_schema(
        tags=["Store - Verification"],
        summary="Submit business verification form",
        description="Submits the required business verification form (PAN, GSTIN, business address, phone, email). "
                    "Completing this e-collection enables access to seller dashboard operations.",
        request=BusinessVerificationSubmitSerializer,
        responses={200: BUSINESS_VERIFICATION_RESPONSE_SCHEMA, 400: STORE_ERROR_SCHEMA, 404: STORE_ERROR_SCHEMA},
    )
    def post(self, request, pk):
        store, membership = get_store_membership(request.user, pk)
        if not store:
            return error_response(
                message="Store not found.",
                code="STORE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if store.is_archived:
            return error_response(
                message="Cannot submit verification for an archived store.",
                code="STORE_ARCHIVED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if store.status == StoreStatus.SUSPENDED:
            return error_response(
                message="Cannot submit verification for a suspended store.",
                code="STORE_SUSPENDED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        serializer = BusinessVerificationSubmitSerializer(data=request.data, context={"store": store})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        verification, _ = BusinessVerification.objects.update_or_create(
            store=store,
            defaults={
                "pan_number": data["pan_number"],
                "gst_number": data["gst_number"],
                "business_address": data["business_address"],
                "business_phone": data["business_phone"],
                "business_email": data["business_email"],
                "status": VerificationStatus.PENDING,
                "submitted_by": request.user,
                "rejection_reason": "",
            },
        )

        return success_response(
            message="Business verification form submitted successfully. It is now pending admin review.",
            data=BusinessVerificationSerializer(verification).data,
            status_code=status.HTTP_200_OK,
        )
