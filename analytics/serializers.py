from rest_framework import serializers


class AnalyticsDateFilterSerializer(serializers.Serializer):
    from_date = serializers.DateField(required=False, format="%Y-%m-%d")
    to_date = serializers.DateField(required=False, format="%Y-%m-%d")

    def validate(self, attrs):
        from_d = attrs.get("from_date")
        to_d = attrs.get("to_date")
        if from_d and to_d and from_d > to_d:
            raise serializers.ValidationError({"from_date": "from_date cannot be later than to_date."})
        return attrs


class RevenueAnalyticsQuerySerializer(AnalyticsDateFilterSerializer):
    period = serializers.ChoiceField(
        choices=["daily", "weekly", "monthly"],
        default="daily",
        required=False,
    )


class ProductPerformanceQuerySerializer(AnalyticsDateFilterSerializer):
    sort_by = serializers.ChoiceField(
        choices=["revenue", "units_sold", "returns"],
        default="revenue",
        required=False,
    )
    limit = serializers.IntegerField(
        default=20,
        min_value=1,
        max_value=100,
        required=False,
    )


class ReportDownloadQuerySerializer(AnalyticsDateFilterSerializer):
    report_type = serializers.ChoiceField(
        choices=["sales", "inventory", "orders"],
        default="sales",
        required=False,
    )
