import logging
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone

from .models import AdEvent, AdEventType, PromotedProduct, StorePaymentSettings, StoreStatus
from products.models import Product, ProductStatus

logger = logging.getLogger(__name__)

AD_CLICK_COST = Decimal("0.10")        # ?100 per 1,000 clicks = ?0.10 per click
AD_ORDER_UNIT_COST = Decimal("1.00")   # ?1.00 per unit ordered
AD_CLICK_COOLDOWN_SECONDS = 900        # 15 minutes


class AdvertisingService:
    """
    Core engine managing promoted products, 15-minute click deduplication in Redis,
    CPA order conversion tracking, and monthly advertising budget ceilings.
    """

    @staticmethod
    def get_client_identifier(request):
        """
        Derives an actor identifier: user ID if authenticated, else client IP.
        """
        if request.user and request.user.is_authenticated:
            return f"usr:{request.user.id}"
        x_forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded:
            ip = x_forwarded.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR", "unknown")
        return f"ip:{ip}"

    @staticmethod
    def get_store_monthly_ad_spend(store, year=None, month=None):
        """
        Calculates total advertising spend for a store in the given calendar month.
        """
        now = timezone.now()
        target_year = int(year) if year else now.year
        target_month = int(month) if month else now.month

        total = AdEvent.objects.filter(
            store=store,
            created_at__year=target_year,
            created_at__month=target_month,
        ).aggregate(total=models.Sum("cost"))["total"]

        return (total or Decimal("0.00")).quantize(Decimal("0.01"))

    @classmethod
    def has_available_budget(cls, store, prospective_cost=Decimal("0.00"), year=None, month=None):
        """
        Checks if the store has sufficient monthly advertising budget to absorb prospective_cost.
        """
        try:
            settings_obj = store.payment_settings
        except StorePaymentSettings.DoesNotExist:
            settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)

        budget = settings_obj.monthly_advertising_budget or Decimal("0.00")
        if budget <= Decimal("0.00"):
            return False

        current_spend = cls.get_store_monthly_ad_spend(store, year=year, month=month)
        return (current_spend + prospective_cost) <= budget

    @classmethod
    def get_eligible_promoted_products(cls, queryset=None, limit=4):
        """
        Retrieves up to `limit` active promoted products that:
        1. Belong to an active store with available advertising budget.
        2. Are active products in stock / active status.
        3. Match the current query / candidate queryset (if provided).
        """
        now = timezone.now()

        promoted_qs = (
            PromotedProduct.objects.filter(
                is_active=True,
                store__status=StoreStatus.ACTIVE,
                store__archived_at__isnull=True,
                product__status=ProductStatus.ACTIVE,
                store__payment_settings__monthly_advertising_budget__gt=Decimal("0.00"),
            )
            .select_related("product", "product__store", "store__payment_settings")
        )

        if queryset is not None:
            candidate_product_ids = queryset.values_list("id", flat=True)
            promoted_qs = promoted_qs.filter(product_id__in=candidate_product_ids)

        eligible_products = []
        seen_store_spends = {}

        for promo in promoted_qs.order_by("-updated_at"):
            store = promo.store
            if store.id not in seen_store_spends:
                budget = store.payment_settings.monthly_advertising_budget
                spend = cls.get_store_monthly_ad_spend(store, now.year, now.month)
                seen_store_spends[store.id] = (budget, spend)

            budget, current_spend = seen_store_spends[store.id]
            if (current_spend + AD_CLICK_COST) <= budget:
                eligible_products.append(promo.product)
                if len(eligible_products) >= limit:
                    break

        return eligible_products

    @classmethod
    def record_ad_click(cls, request, product):
        """
        Records a Cost-Per-Click (CPC) ad event (?0.10) with 15-minute Redis deduplication.
        Returns a dict indicating whether the click was billable or rejected/deduplicated.
        """
        if not product or not product.store:
            return {"recorded": False, "reason": "invalid_product"}

        store = product.store
        if store.status != StoreStatus.ACTIVE or store.archived_at is not None:
            return {"recorded": False, "reason": "store_inactive"}

        # 1. Product must be in active promotion
        is_promoted = PromotedProduct.objects.filter(
            store=store,
            product=product,
            is_active=True,
        ).exists()
        if not is_promoted:
            return {"recorded": False, "reason": "not_promoted"}

        # 2. Check store budget availability
        if not cls.has_available_budget(store, prospective_cost=AD_CLICK_COST):
            return {"recorded": False, "reason": "budget_exhausted"}

        # 3. 15-minute Redis Cooldown (atomic lock)
        client_id = cls.get_client_identifier(request)
        cooldown_key = f"ad_clk:{client_id}:{product.id}"
        is_new_click = cache.add(cooldown_key, 1, timeout=AD_CLICK_COOLDOWN_SECONDS)

        if not is_new_click:
            return {"recorded": False, "reason": "cooldown_active"}

        # 4. Billable event creation
        event = AdEvent.objects.create(
            store=store,
            product=product,
            event_type=AdEventType.CLICK,
            cost=AD_CLICK_COST,
            units=1,
            user=request.user if (request.user and request.user.is_authenticated) else None,
            metadata={
                "client_id": client_id,
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
            },
        )
        logger.info(
            "Ad click recorded: Store=%s, Product=%s, Client=%s, Cost=?%s",
            store.id, product.id, client_id, AD_CLICK_COST
        )
        return {"recorded": True, "event_id": event.id, "cost": str(AD_CLICK_COST)}

    @classmethod
    def record_order_conversions(cls, order):
        """
        Records Cost-Per-Action (CPA) ad events (?1.00 per unit ordered)
        for all promoted items in a confirmed order.
        """
        if not order or not order.store:
            return []

        store = order.store
        promoted_items = order.items.filter(is_promoted=True).select_related("product")
        if not promoted_items.exists():
            return []

        recorded_events = []
        now = timezone.now()

        for item in promoted_items:
            unit_qty = item.quantity or 1
            cost = (AD_ORDER_UNIT_COST * unit_qty).quantize(Decimal("0.01"))

            # Calculate available budget headroom
            current_spend = cls.get_store_monthly_ad_spend(store, now.year, now.month)
            try:
                settings_obj = store.payment_settings
            except StorePaymentSettings.DoesNotExist:
                settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)

            budget = settings_obj.monthly_advertising_budget or Decimal("0.00")
            headroom = max(Decimal("0.00"), budget - current_spend)
            actual_cost = min(cost, headroom)

            if actual_cost > Decimal("0.00"):
                event = AdEvent.objects.create(
                    store=store,
                    product=item.product,
                    event_type=AdEventType.ORDER,
                    cost=actual_cost,
                    units=unit_qty,
                    user=order.user,
                    order=order,
                    metadata={"order_number": order.order_number, "item_id": item.id},
                )
                recorded_events.append(event)
                logger.info(
                    "Ad order conversion recorded: Store=%s, Order=%s, Product=%s, Units=%d, Cost=?%s",
                    store.id, order.order_number, item.product_id, unit_qty, actual_cost
                )

        return recorded_events

    @classmethod
    def get_store_advertising_analytics(cls, store, year=None, month=None):
        """
        Aggregates advertising performance metrics for a store:
        budget, current spend, clicks, orders, units sold, conversion rate, and product stats.
        """
        now = timezone.now()
        target_year = int(year) if year else now.year
        target_month = int(month) if month else now.month

        try:
            settings_obj = store.payment_settings
        except StorePaymentSettings.DoesNotExist:
            settings_obj, _ = StorePaymentSettings.objects.get_or_create(store=store)

        monthly_budget = (settings_obj.monthly_advertising_budget or Decimal("0.00")).quantize(Decimal("0.01"))

        events_qs = AdEvent.objects.filter(
            store=store,
            created_at__year=target_year,
            created_at__month=target_month,
        )

        clicks_count = events_qs.filter(event_type=AdEventType.CLICK).count()
        order_events = events_qs.filter(event_type=AdEventType.ORDER)
        orders_count = order_events.count()
        units_sold = order_events.aggregate(total_units=models.Sum("units"))["total_units"] or 0

        current_spend = (events_qs.aggregate(total=models.Sum("cost"))["total"] or Decimal("0.00")).quantize(Decimal("0.01"))
        remaining_budget = max(Decimal("0.00"), monthly_budget - current_spend).quantize(Decimal("0.01"))

        conversion_rate = round((orders_count / clicks_count * 100), 2) if clicks_count > 0 else 0.0

        # Per-product performance
        promoted_records = (
            PromotedProduct.objects.filter(store=store)
            .select_related("product")
            .order_by("-updated_at")
        )

        products_data = []
        for promo in promoted_records:
            prod_events = events_qs.filter(product=promo.product)
            prod_clicks = prod_events.filter(event_type=AdEventType.CLICK).count()
            prod_orders = prod_events.filter(event_type=AdEventType.ORDER)
            prod_orders_count = prod_orders.count()
            prod_units = prod_orders.aggregate(u=models.Sum("units"))["u"] or 0
            prod_spend = (prod_events.aggregate(s=models.Sum("cost"))["s"] or Decimal("0.00")).quantize(Decimal("0.01"))

            products_data.append({
                "promoted_product_id": promo.id,
                "product_id": promo.product.id,
                "product_name": promo.product.name,
                "product_price": str(promo.product.price),
                "is_active": promo.is_active,
                "clicks": prod_clicks,
                "orders": prod_orders_count,
                "units_sold": prod_units,
                "spend": str(prod_spend),
                "created_at": promo.created_at.isoformat(),
            })

        return {
            "period": f"{target_year}-{target_month:02d}",
            "monthly_budget": str(monthly_budget),
            "current_spend": str(current_spend),
            "remaining_budget": str(remaining_budget),
            "total_clicks": clicks_count,
            "total_orders": orders_count,
            "total_units_sold": units_sold,
            "conversion_rate": conversion_rate,
            "cpc_rate": str(AD_CLICK_COST),
            "cpa_rate": str(AD_ORDER_UNIT_COST),
            "promoted_products_count": promoted_records.count(),
            "products": products_data,
        }
