from decimal import Decimal
from django.db.models import Avg, Count
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.responses import error_response, success_response
from products.models import Product, ProductStatus

from .models import Review
from .pagination import ReviewPagination
from .serializers import (
    REVIEW_DELETE_RESPONSE_SCHEMA,
    REVIEW_DETAIL_RESPONSE_SCHEMA,
    REVIEW_ERROR_SCHEMA,
    REVIEW_PAGINATED_LIST_RESPONSE_SCHEMA,
    ReviewCreateSerializer,
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

        # Compute rating summary for all product reviews (unfiltered)
        agg_stats = all_product_reviews.aggregate(
            avg_rating=Avg("rating"),
            total_reviews=Count("id"),
        )
        avg = agg_stats["avg_rating"]
        average_rating = round(float(avg), 2) if avg is not None else 0.0
        total_reviews = agg_stats["total_reviews"] or 0

        # Star breakdown distribution
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

        # Filtered queryset for pagination
        queryset = all_product_reviews.select_related("user", "product")

        # 1. Exact star rating filter
        rating_param = request.query_params.get("rating")
        if rating_param:
            try:
                r_val = int(rating_param)
                if 1 <= r_val <= 5:
                    queryset = queryset.filter(rating=r_val)
            except (ValueError, TypeError):
                pass

        # 2. Minimum star rating filter
        min_rating_param = request.query_params.get("min_rating")
        if min_rating_param:
            try:
                mr_val = int(min_rating_param)
                if 1 <= mr_val <= 5:
                    queryset = queryset.filter(rating__gte=mr_val)
            except (ValueError, TypeError):
                pass

        # 3. Lettering filter (title starts with letter)
        letter_param = request.query_params.get("letter", "").strip()
        if letter_param:
            queryset = queryset.filter(title__istartswith=letter_param[0])

        # 4. Verified buyer filter
        verified_param = request.query_params.get("verified_only")
        if verified_param and str(verified_param).lower() in ("true", "1", "yes"):
            queryset = queryset.filter(is_verified_buyer=True)

        # 5. Ordering
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
        description="Allows an authenticated user to submit a rating (1 to 5 stars), title, and description for a product. Only one review per user per product is permitted. Automatically verifies buyer purchase status.",
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

        # Check for existing review by this user
        existing_review = Review.objects.filter(product=product, user=request.user).first()
        if existing_review:
            return error_response(
                message="You have already reviewed this product. You can update your existing review.",
                code="DUPLICATE_REVIEW",
                details={"existing_review_id": existing_review.id},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        review = Review.objects.create(
            product=product,
            user=request.user,
            rating=data["rating"],
            title=data["title"],
            description=data["description"],
        )

        return success_response(
            message="Review submitted successfully.",
            data=ReviewSerializer(review).data,
            status_code=status.HTTP_201_CREATED,
        )


class ReviewDetailView(APIView):
    """
    Retrieve, update, or delete a single review.
    """
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
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").first()
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
        description="Allows the review author to update the star rating, title, or description. Recomputes product rating average.",
        request=ReviewUpdateSerializer,
        responses={200: REVIEW_DETAIL_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 403: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def put(self, request, review_pk):
        return self._update_review(request, review_pk)

    @extend_schema(
        tags=["Reviews & Ratings"],
        summary="Partially update your review",
        description="Allows the review author to update specific fields of their review (rating, title, description). Recomputes product rating average.",
        request=ReviewUpdateSerializer,
        responses={200: REVIEW_DETAIL_RESPONSE_SCHEMA, 400: REVIEW_ERROR_SCHEMA, 403: REVIEW_ERROR_SCHEMA, 404: REVIEW_ERROR_SCHEMA},
    )
    def patch(self, request, review_pk):
        return self._update_review(request, review_pk)

    def _update_review(self, request, review_pk):
        review = Review.objects.filter(pk=review_pk).select_related("product", "user").first()
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

        serializer = ReviewUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "rating" in data:
            review.rating = data["rating"]
        if "title" in data:
            review.title = data["title"]
        if "description" in data:
            review.description = data["description"]

        review.save()

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
            .order_by("-created_at")
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        serializer = ReviewSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
