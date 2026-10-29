from drf_spectacular.utils import extend_schema_field, inline_serializer
from rest_framework import serializers

from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    """Full review serializer for public reading and user responses."""
    product_name = serializers.CharField(source="product.name", read_only=True)
    user_name = serializers.SerializerMethodField()

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



class RatingSummarySerializer(serializers.Serializer):
    average_rating = serializers.FloatField(help_text="Average product rating (0.0 to 5.0).")
    total_reviews = serializers.IntegerField(help_text="Total number of reviews submitted.")
    rating_breakdown = serializers.DictField(
        child=serializers.IntegerField(),
        help_text="Counts of reviews keyed by star rating ('1', '2', '3', '4', '5').",
    )


# ==============================================================================
# SPECTACULAR RESPONSE ENVELOPES
# ==============================================================================

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
    role = serializers.ChoiceField(choices=["user", "model"])
    content = serializers.CharField(max_length=300)


class ReviewChatRequestSerializer(serializers.Serializer):
    question = serializers.CharField(
        max_length=300,
        required=True,
        help_text="Customer question about this product's reviews.",
    )
    history = serializers.ListField(
        child=ReviewChatHistoryItemSerializer(),
        required=False,
        default=list,
        max_length=5,
        help_text="Client-side ephemeral prior chat turns (max 5). Not saved server-side.",
    )


REVIEW_SUMMARY_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewSummaryResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": ProductReviewSummarySerializer(),
    },
)

REVIEW_CHAT_RESPONSE_SCHEMA = inline_serializer(
    name="ReviewChatResponse",
    fields={
        "success": serializers.BooleanField(default=True),
        "message": serializers.CharField(),
        "data": inline_serializer(
            name="ReviewChatResponseData",
            fields={
                "reply": serializers.CharField(help_text="Concise review-grounded reply."),
                "is_guardrail_refusal": serializers.BooleanField(help_text="True if question was out of scope or triggered guardrails."),
            },
        ),
    },
)
