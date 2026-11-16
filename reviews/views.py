from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from products.models import Product, ProductStatus

from .ai_service import FALLBACK_REFUSAL, ReviewAIService
from .models import ProductReviewSummary, Review, ReviewImage
from .pagination import ReviewPagination
from .validators import (
    MAX_REVIEW_IMAGE_SIZE,
    MAX_REVIEW_IMAGES_COUNT,
    validate_review_image,
)
from .serializers import (
    REVIEW_CHAT_RESPONSE_SCHEMA,
    REVIEW_DELETE_RESPONSE_SCHEMA,
    REVIEW_DETAIL_RESPONSE_SCHEMA,
    REVIEW_ERROR_SCHEMA,
    REVIEW_IMAGE_LIST_RESPONSE_SCHEMA,
    REVIEW_IMAGE_RESPONSE_SCHEMA,
    REVIEW_PAGINATED_LIST_RESPONSE_SCHEMA,
    REVIEW_SUMMARY_RESPONSE_SCHEMA,
    ProductReviewSummarySerializer,
    ReviewChatRequestSerializer,
    ReviewCreateSerializer,
    ReviewImageSerializer,
    ReviewImageUploadSerializer,
    ReviewSerializer,
    ReviewUpdateSerializer,
)


class ProductReviewListCreateView(APIView):
    """
    Public and customer review endpoint for a specific product.
    - GET: Paginated list of reviews with rating breakdown, star filtering, and title lettering.
    - POST: Submit a 1-to-5 star review with title and description (authenticated).
    """
    pagination_class = ReviewPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="List reviews for a product",
        description="Returns paginated reviews for a product. Includes overall rating summary and star breakdown. Supports filtering by rating, title letter, verified buyer status, and sort ordering.",
        parameters=[
            OpenApiParameter(name="rating", description="Filter by exact star rating (1 to 5)", required=False, type=int),
            OpenApiParameter(name="min_rating", description="Filter by minimum star rating (1 to 5)", required=False, type=int),
            OpenApiParameter(name="letter", description="Filter reviews where title starts with this letter (case-insensitive)", required=False, type=str),
            OpenApiParameter(name="verified_only", description="If true, return only verified buyer reviews", required=False, type=bool),
            OpenApiParameter(name="ordering", description="Sort by: '-created_at' (newest), 'created_at' (oldest), '-rating' (highest), 'rating' (lowest)", required=False, type=str),
            OpenApiParameter(name="page", description="Page number", required=False, type=int),
            OpenApiParameter(name="page_size", description="Page size (max 50, default 10)", required=False, type=int),
        ],
        responses={200: REVIEW_PAGINATED_LIST_RESPONSE_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def get(self, request, product_pk):
        product = Product.objects.filter(pk=product_pk, status=ProductStatus.ACTIVE).first()
        if not product:
            return error_response(
                message="Product not found or is currently inactive.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        all_product_reviews = Review.objects.filter(product=product)

        agg_stats = all_product_reviews.aggregate(
            avg_rating=Avg("rating"),
            total_reviews=Count("id"),
        )
        avg = agg_stats["avg_rating"]
        average_rating = round(float(avg), 2) if avg is not None else 0.0
        total_reviews = agg_stats["total_reviews"] or 0

        breakdown_counts = {str(star): 0 for star in range(1, 6)}
        distribution_agg = all_product_reviews.values("rating").annotate(count=Count("id"))
        for row in distribution_agg:
            r_str = str(row["rating"])
            if r_str in breakdown_counts:
                breakdown_counts[r_str] = row["count"]

        rating_summary = {
            "average_rating": average_rating,
            "total_reviews": total_reviews,
            "rating_breakdown": breakdown_counts,
        }

        queryset = all_product_reviews.select_related("user", "product").prefetch_related("images")

        rating_param = request.query_params.get("rating")
        if rating_param:
            try:
                r_val = int(rating_param)
                if 1 <= r_val <= 5:
                    queryset = queryset.filter(rating=r_val)
            except (ValueError, TypeError):
                pass

        min_rating_param = request.query_params.get("min_rating")
        if min_rating_param:
            try:
                mr_val = int(min_rating_param)
                if 1 <= mr_val <= 5:
                    queryset = queryset.filter(rating__gte=mr_val)
            except (ValueError, TypeError):
                pass

        letter_param = request.query_params.get("letter", "").strip()
        if letter_param:
            queryset = queryset.filter(title__istartswith=letter_param[0])

        verified_param = request.query_params.get("verified_only")
        if verified_param and str(verified_param).lower() in ("true", "1", "yes"):
            queryset = queryset.filter(is_verified_buyer=True)

        ordering_param = request.query_params.get("ordering", "-created_at").strip().lower()
        valid_orderings = {
            "-created_at": "-created_at",
            "newest": "-created_at",
            "created_at": "created_at",
            "oldest": "created_at",
            "-rating": "-rating",
            "highest": "-rating",
            "rating": "rating",
            "lowest": "rating",
        }
        order_field = valid_orderings.get(ordering_param, "-created_at")
        queryset = queryset.order_by(order_field)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ReviewSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data, rating_summary=rating_summary)

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Post a review for a product",
        description=(
            "Allows an authenticated user to submit a rating (1 to 5 stars), title, description, "
            "and up to 5 optional customer photos (max 1MB each, JPEG/PNG/WEBP). "
            "Only one review per user per product is permitted. Automatically verifies buyer purchase status."
        ),
        request=ReviewCreateSerializer,
        responses={201: REVIEW_DETAIL_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def post(self, request, product_pk):
        product = Product.objects.filter(pk=product_pk, status=ProductStatus.ACTIVE).first()
        if not product:
            return error_response(
                message="Product not found or is currently inactive.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        existing_review = Review.objects.filter(product=product, user=request.user).first()
        if existing_review:
            return error_response(
                message="You have already reviewed this product. You can update your existing review.",
                code="DUPLICATE_REVIEW",
                details={"existing_review_id": existing_review.id},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        image_files = (
            request.FILES.getlist("images")
            or request.FILES.getlist("images[]")
            or request.FILES.getlist("image")
        )
        if image_files and hasattr(data, "setlist"):
            data.setlist("images", image_files)

        serializer = ReviewCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        if len(image_files) > MAX_REVIEW_IMAGES_COUNT:
            return error_response(
                message=f"Maximum of {MAX_REVIEW_IMAGES_COUNT} images allowed per review. Provided: {len(image_files)}.",
                code="TOO_MANY_IMAGES",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        for img in image_files:
            try:
                validate_review_image(img, MAX_REVIEW_IMAGE_SIZE)
            except ValidationError as exc:
                return error_response(
                    message=exc.message if hasattr(exc, "message") else str(exc),
                    code="INVALID_IMAGE",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            review = Review.objects.create(
                product=product,
                user=request.user,
                rating=validated_data["rating"],
                title=validated_data["title"],
                description=validated_data["description"],
            )
            for img in image_files:
                ReviewImage.objects.create(review=review, image=img)

        review.refresh_from_db()
        return success_response(
            message="Review submitted successfully.",
            data=ReviewSerializer(review).data,
            status_code=status.HTTP_201_CREATED,
        )


class ReviewDetailView(APIView):
    """
    Retrieve, update, or delete a single review.
    """
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method in ("PUT", "PATCH", "DELETE"):
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Get review details",
        description="Retrieves a specific review by review ID.",
        responses={200: REVIEW_DETAIL_RESPONSE_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def get(self, request, review_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").prefetch_related("images").first()
        if not review:
            return error_response(
                message="Review not found.",
                code="REVIEW_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return success_response(
            message="Review retrieved successfully.",
            data=ReviewSerializer(review).data,
        )

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Update your review (full)",
        description="Allows the review author to update the star rating, title, description, or attach new photos (max 5 total). Recomputes product rating average.",
        request=ReviewUpdateSerializer,
        responses={200: REVIEW_DETAIL_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 403: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def put(self, request, review_pk):
        return self._update_review(request, review_pk)

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Partially update your review",
        description="Allows the review author to update specific fields of their review (rating, title, description, photos). Recomputes product rating average.",
        request=ReviewUpdateSerializer,
        responses={200: REVIEW_DETAIL_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 403: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def patch(self, request, review_pk):
        return self._update_review(request, review_pk)

    def _update_review(self, request, review_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").prefetch_related("images").first()
        if not review:
            return error_response(
                message="Review not found.",
                code="REVIEW_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if review.user != request.user and not request.user.is_staff:
            return error_response(
                message="You do not have permission to modify this review.",
                code="PERMISSION_DENIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        image_files = (
            request.FILES.getlist("images")
            or request.FILES.getlist("images[]")
            or request.FILES.getlist("image")
        )
        if image_files and hasattr(data, "setlist"):
            data.setlist("images", image_files)

        serializer = ReviewUpdateSerializer(data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data

        if image_files:
            current_count = review.images.count()
            if current_count + len(image_files) > MAX_REVIEW_IMAGES_COUNT:
                return error_response(
                    message=f"Cannot add {len(image_files)} image(s). Review already has {current_count} (maximum {MAX_REVIEW_IMAGES_COUNT}).",
                    code="TOO_MANY_IMAGES",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            for img in image_files:
                try:
                    validate_review_image(img, MAX_REVIEW_IMAGE_SIZE)
                except ValidationError as exc:
                    return error_response(
                        message=exc.message if hasattr(exc, "message") else str(exc),
                        code="INVALID_IMAGE",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )

        with transaction.atomic():
            if "rating" in validated_data:
                review.rating = validated_data["rating"]
            if "title" in validated_data:
                review.title = validated_data["title"]
            if "description" in validated_data:
                review.description = validated_data["description"]
            review.save()

            if image_files:
                for img in image_files:
                    ReviewImage.objects.create(review=review, image=img)

        review.refresh_from_db()
        return success_response(
            message="Review updated successfully.",
            data=ReviewSerializer(review).data,
        )

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Delete your review",
        description="Deletes a review and updates the product average rating and review count. Only the author or staff can delete.",
        responses={200: REVIEW_DELETE_RESPONSE_SCHEMA, 403: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def delete(self, request, review_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").first()
        if not review:
            return error_response(
                message="Review not found.",
                code="REVIEW_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if review.user != request.user and not request.user.is_staff:
            return error_response(
                message="You do not have permission to delete this review.",
                code="PERMISSION_DENIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        review.delete()

        return success_response(
            message="Review deleted successfully.",
            data=None,
        )


class ReviewImageUploadView(APIView):
    """
    Dedicated endpoint to upload additional photos for an existing review (up to 5 total, max 1MB each).
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Upload photos for an existing review",
        description="Allows the review author to upload images for their existing review (max 1MB per image, up to 5 images total per review).",
        request=ReviewImageUploadSerializer,
        responses={
            201: REVIEW_DETAIL_RESPONSE_SCHEMA,
            400: REVIEW_ERROR_SCHEMA,
            403: REVIEW_ERROR_SCHEMA,
            404: REVIEW_ERROR_SCHEMA,
        },
    )
    def post(self, request, review_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").prefetch_related("images").first()
        if not review:
            return error_response(
                message="Review not found.",
                code="REVIEW_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if review.user != request.user and not request.user.is_staff:
            return error_response(
                message="You do not have permission to upload photos to this review.",
                code="PERMISSION_DENIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        image_files = (
            request.FILES.getlist("images")
            or request.FILES.getlist("images[]")
            or request.FILES.getlist("image")
        )
        if not image_files:
            return error_response(
                message="At least one image file is required.",
                code="IMAGE_REQUIRED",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        current_count = review.images.count()
        if current_count + len(image_files) > MAX_REVIEW_IMAGES_COUNT:
            return error_response(
                message=f"Cannot add {len(image_files)} image(s). Review already has {current_count} (maximum {MAX_REVIEW_IMAGES_COUNT}).",
                code="TOO_MANY_IMAGES",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        for img in image_files:
            try:
                validate_review_image(img, MAX_REVIEW_IMAGE_SIZE)
            except ValidationError as exc:
                return error_response(
                    message=exc.message if hasattr(exc, "message") else str(exc),
                    code="INVALID_IMAGE",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            for img in image_files:
                ReviewImage.objects.create(review=review, image=img)

        review.refresh_from_db()
        return success_response(
            message=f"{len(image_files)} image(s) uploaded successfully.",
            data=ReviewSerializer(review).data,
            status_code=status.HTTP_201_CREATED,
        )


class ReviewImageDetailView(APIView):
    """
    Delete a specific review photo.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Delete a review image",
        description="Allows the review author to delete a specific photo attached to their review.",
        responses={
            200: REVIEW_DETAIL_RESPONSE_SCHEMA,
            403: REVIEW_ERROR_SCHEMA,
            404: REVIEW_ERROR_SCHEMA,
        },
    )
    def delete(self, request, review_pk, image_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").prefetch_related("images").first()
        if not review:
            return error_response(
                message="Review not found.",
                code="REVIEW_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if review.user != request.user and not request.user.is_staff:
            return error_response(
                message="You do not have permission to delete photos from this review.",
                code="PERMISSION_DENIED",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        image_obj = review.images.filter(pk=image_pk).first()
        if not image_obj:
            return error_response(
                message="Review image not found.",
                code="IMAGE_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        image_obj.delete()
        review.refresh_from_db()
        return success_response(
            message="Review image deleted successfully.",
            data=ReviewSerializer(review).data,
        )



class CustomerMyReviewsView(APIView):
    """
    Returns all reviews written by the authenticated user with pagination.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = ReviewPagination

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="List my reviews",
        description="Returns paginated reviews submitted by the authenticated customer.",
        parameters=[
            OpenApiParameter(name="page", description="Page number", required=False, type=int),
            OpenApiParameter(name="page_size", description="Page size (max 50, default 10)", required=False, type=int),
        ],
        responses={200: REVIEW_PAGINATED_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request):
        queryset = (
            Review.objects.filter(user=request.user)
            .select_related("product")
            .prefetch_related("images")
            .order_by("-created_at")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ReviewSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class ProductReviewSummaryView(APIView):
    """
    Returns the precomputed 24-hour AI review consensus summary for an active product.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Get 24-hour AI review summary for a product",
        description="Returns an aggregated AI summary including pros, cons, sizing verdict, and sentiment distribution for products that have reviews. Updated daily.",
        responses={200: REVIEW_SUMMARY_RESPONSE_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def get(self, request, product_pk):
        product = Product.objects.filter(pk=product_pk, status=ProductStatus.ACTIVE).first()
        if not product:
            return error_response(
                message="Product not found or is currently inactive.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        summary = getattr(product, "review_summary", None)
        if not summary:
            return error_response(
                message="No review summary has been generated for this product yet.",
                code="SUMMARY_NOT_AVAILABLE",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        serializer = ProductReviewSummarySerializer(summary)
        return success_response(
            data=serializer.data,
            message="Product review summary retrieved successfully.",
        )


class ProductReviewChatView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "review_chat"

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Ask product & review chatbot a question (stateless, guardrailed)",
        description=(
            "Stateless, guardrailed AI chatbot endpoint that answers shopper questions using product specifications, "
            "attributes, pricing, and customer reviews.\n\n"
            "Key Features & Guardrails:\n"
            "- Operates via Gemini 1.5 Flash with deterministic prompt injection and scope-enforcing guardrails.\n"
            "- Answers questions based on product details (name, category, description, price, sizes, stock, refund policy) "
            "and buyer reviews.\n"
            "- Fully functional even when a product does not yet have customer reviews (gracefully grounds answers in product details).\n"
            "- Supports conversational continuity with a sliding context window of up to 3 prior messages (`history`).\n"
            "- Stateless and ephemeral: chat history is processed in-memory and never stored on the server."
        ),
        parameters=[
            OpenApiParameter(
                name="product_pk",
                type=int,
                location=OpenApiParameter.PATH,
                description="Unique integer primary key of the active product to inquire about.",
            ),
        ],
        request=ReviewChatRequestSerializer,
        responses={200: REVIEW_CHAT_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def post(self, request, product_pk):
        product = Product.objects.filter(pk=product_pk, status=ProductStatus.ACTIVE).first()
        if not product:
            return error_response(
                message="Product not found or is currently inactive.",
                code="PRODUCT_NOT_FOUND",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if product.review_count == 0 and Review.objects.filter(product=product).exists():
            product.update_rating_stats()

        serializer = ReviewChatRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                message="Invalid review chat request payload.",
                code="INVALID_PAYLOAD",
                details=serializer.errors,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        question = serializer.validated_data["question"]
        history = serializer.validated_data.get("history", [])

        is_safe, refusal_reason = ReviewAIService.check_preflight_guardrails(question)
        if not is_safe:
            return success_response(
                data={
                    "reply": refusal_reason,
                    "is_guardrail_refusal": True,
                },
                message="Chat response generated successfully.",
            )

        summary = getattr(product, "review_summary", None)
        reviews_sample = list(
            Review.objects.filter(product=product)
            .values("rating", "title", "description", "is_verified_buyer")[:25]
        )

        reply = ReviewAIService.ask_review_question(
            product=product,
            summary_obj=summary,
            reviews_sample=reviews_sample,
            question=question,
            history=history,
        )

        is_refusal = (
            "I can only answer questions regarding" in reply
            or reply == FALLBACK_REFUSAL
        )

        return success_response(
            data={
                "reply": reply,
                "is_guardrail_refusal": is_refusal,
            },
            message="Chat response generated successfully.",
        )
