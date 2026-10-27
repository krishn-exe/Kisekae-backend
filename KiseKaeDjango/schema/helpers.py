from drf_spectacular.utils import inline_serializer
from rest_framework import serializers

ERROR_SCHEMA = inline_serializer(
    name="StandardErrorEnvelope",
    fields={
        "success": serializers.BooleanField(default=False),
        "message": serializers.CharField(),
        "error": inline_serializer(
            name="StandardErrorDetail",
            fields={
                "code": serializers.CharField(),
                "details": serializers.JSONField(allow_null=True),
            },
        ),
    },
)

def api_response(data_serializer=None, name="ApiResponse", many=False, message="Operation successful"):
    """
    Returns an enveloped inline serializer:
    {
        "success": true,
        "message": "...",
        "data": <data_serializer> | null
    }
    """
    if data_serializer is None:
        data_field = serializers.JSONField(allow_null=True, required=False)
    elif isinstance(data_serializer, serializers.BaseSerializer):
        data_field = data_serializer
    elif isinstance(data_serializer, type) and issubclass(data_serializer, serializers.BaseSerializer):
        data_field = data_serializer(many=many)
    else:
        data_field = data_serializer

    return inline_serializer(
        name=name,
        fields={
            "success": serializers.BooleanField(default=True),
            "message": serializers.CharField(default=message),
            "data": data_field,
        },
    )

def paginated_response(item_serializer, name="PaginatedResponse"):
    """
    Returns standard paginated inline serializer:
    {
        "count": int,
        "next": str,
        "previous": str,
        "results": [item_serializer]
    }
    """
    item_field = item_serializer(many=True) if isinstance(item_serializer, type) and issubclass(item_serializer, serializers.BaseSerializer) else item_serializer
    return inline_serializer(
        name=name,
        fields={
            "count": serializers.IntegerField(),
            "next": serializers.CharField(allow_null=True),
            "previous": serializers.CharField(allow_null=True),
            "results": item_field,
        },
    )
