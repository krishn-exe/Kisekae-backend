from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from .models import Review, ReviewImage


class ReviewImageSerializer(serializers.ModelSerializer):
    image_url = serializers.CharField(read_only=True)

    class Meta:
        model = ReviewImage
        fields = ["id", "image_url", "created_at"]


class ReviewSerializer(serializers.ModelSerializer):
    """Full review serializer for public reading and user responses."""
    product_name = serializers.CharField(source="product.name", read_only=True)
    user_name = serializers.SerializerMethodField()
    images = ReviewImageSerializer(many=True, read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "product",
            "product_name",
            "user",
            "user_name",
            "rating",
            "title",
            "description",
            "is_verified_buyer",
            "images",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "product",
            "product_name",
            "user",
            "user_name",
            "is_verified_buyer",
            "images",
            "created_at",
            "updated_at",
        ]

    @extend_schema_field(serializers.CharField())
    def get_user_name(self, obj) -> str:
        if not obj.user:
            return "Anonymous"
        name = getattr(obj.user, "name", "").strip()
        if name:
            return name
        email = getattr(obj.user, "email", "")
        return email.split("@")[0] if "@" in email else "Customer"


class ReviewCreateSerializer(serializers.Serializer):
    """Payload to create a new product review."""
    rating = serializers.IntegerField(
        min_value=1,
        max_value=5,
        help_text="Star rating from 1 to 5 (inclusive).",
    )
    title = serializers.CharField(
        max_length=255,
        min_length=2,
        help_text="Short headline summarizing the review (e.g. 'Loved the fabric and fit').",
    )
    description = serializers.CharField(
        min_length=5,
        help_text="Full text feedback detailing customer experience.",
    )
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        default=list,
        max_length=5,
        help_text="Optional customer review photos (up to 5 images, max 1MB each).",
    )


class ReviewUpdateSerializer(serializers.Serializer):
    """Payload to update an existing product review."""
    rating = serializers.IntegerField(
        min_value=1,
        max_value=5,
        required=False,
        help_text="Updated star rating (1-5).",
    )
    title = serializers.CharField(
        max_length=255,
        min_length=2,
        required=False,
        help_text="Updated review headline.",
    )
    description = serializers.CharField(
        min_length=5,
        required=False,
        help_text="Updated review description.",
    )
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        max_length=5,
        help_text="Optional new review photos to attach (up to 5 images total on the review, max 1MB each).",
    )


class ReviewImageUploadSerializer(serializers.Serializer):
    """Upload photos for an existing review."""
    image = serializers.ImageField(
        required=False,
        help_text="Single review image file (max 1MB).",
    )
    images = serializers.ListField(
        child=serializers.ImageField(),
        required=False,
        max_length=5,
        help_text="Multiple review image files (max 1MB each, up to 5 total on the review).",
    )




class RatingSummarySerializer(serializers.Serializer):
    average_rating = serializers.FloatField(help_text="Average product rating (0.0 to 5.0).")
    total_reviews = serializers.IntegerField(help_text="Total number of reviews submitted.")
    rating_breakdown = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Counts of reviews keyed by star rating ('1', '2', '3', '4', '5').",
    )



REVIEW_ERROR_SCHEMA = inline_serializer(
    name="ReviewErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="ReviewErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)

REVIEW_DETAIL_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewDetailResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ReviewSerializer(),
    },
)

REVIEW_IMAGE_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewImageResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ReviewImageSerializer(),
    },
)

REVIEW_IMAGE_LIST_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewImageListResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ReviewImageSerializer(many=True),
    },
)

REVIEW_PAGINATED_LIST_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewPaginatedListResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": inline_serializer(
            name="ReviewPaginatedListData",
            fields={
                "rating_summary": RatingSummarySerializer(),
                "count": serializers.IntegerField(help_text="Total matching reviews count."),
                "total_pages": serializers.IntegerField(help_text="Total number of pages."),
                "current_page": serializers.IntegerField(help_text="Current page index."),
                "next": serializers.CharField(allow_null=True, help_text="URL for next page of reviews."),
                "previous": serializers.CharField(allow_null=True, help_text="URL for previous page of reviews."),
                "results": ReviewSerializer(many=True),
            },
        ),
    },
)

REVIEW_DELETE_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewDeleteResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": serializers.JSONField(allow_null=True),
    },
)


class ProductReviewSummarySerializer(serializers.ModelSerializer):
    """Serializer for precomputed 24-hour review consensus summary."""
    class Meta:
        from .models import ProductReviewSummary
        model = ProductReviewSummary
        fields = [
            "product_id",
            "summary_text",
            "pros",
            "cons",
            "sizing_verdict",
            "sentiment_distribution",
            "review_count_at_summary",
            "generated_at",
        ]


class ReviewChatHistoryItemSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=["user", "model", "assistant"],
        help_text="Speaker identity in the prior conversation turn. Accepts 'user' (shopper/customer), or 'model' / 'assistant' (AI assistant).",
    )
    content = serializers.CharField(
        max_length=300,
        help_text="Text content of the message sent in this turn (maximum 300 characters).",
    )


class ReviewChatRequestSerializer(serializers.Serializer):
    question = serializers.CharField(
        max_length=300,
        required=True,
        help_text="Shopper's current question about the product specifications, materials, sizing, pricing, or customer reviews (maximum 300 characters).",
    )
    history = serializers.ListField(
        child=ReviewChatHistoryItemSerializer(),
        required=False,
        default=list,
        max_length=3,
        help_text="Optional conversation context containing up to 3 prior messages (alternating user/assistant turns) for conversational continuity. Ephemeral and not stored server-side.",
    )


REVIEW_SUMMARY_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewSummaryResponse",
    fields={
        "success": serializers.BooleanField(default=True, help_text="Indicates whether the request succeeded."),
        "message": serializers.CharField(help_text="Human-readable status message."),
        "data": ProductReviewSummarySerializer(),
    },
)

REVIEW_CHAT_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewChatResponse",
    fields={
        "success": serializers.BooleanField(default=True, help_text="Indicates whether the query succeeded."),
        "message": serializers.CharField(help_text="Human-readable status message."),
        "data": inline_serializer(
            name="ReviewChatResponseData",
            fields={
                "reply": serializers.CharField(help_text="Concise answer grounded in product specifications, attributes, and customer reviews."),
                "is_guardrail_refusal": serializers.BooleanField(help_text="True if question was out of scope or triggered guardrails."),
            },
        ),
    },
)
