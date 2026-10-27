import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_pending_order_task(order_id):
    """
    Checks if a pending order has exceeded its 5-minute reservation TTL.
    If unpaid, releases reserved stock and marks the order as FAILED.
    """
    from .models import Order, OrderStatus
    from .services import OrderService

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(pk=order_id).first()
            if not order:
                return
            if order.status == OrderStatus.PENDING and order.expires_at and timezone.now() >= order.expires_at:
                logger.info("Expiring pending order %s (5-min window elapsed)", order.order_number)
                OrderService.expire_pending_order(order)
    except Exception as exc:
        logger.exception("Error expiring pending order %s: %s", order_id, exc)


@shared_task
def expire_stale_pending_orders_task():
    """
    Periodic safety sweeper for any un-expired pending orders beyond 5 minutes.
    """
    from .models import Order, OrderStatus
    from .services import OrderService

    stale_orders = list(Order.objects.filter(
        status=OrderStatus.PENDING,
        expires_at__lte=timezone.now(),
    ).values_list("id", flat=True))

    count = 0
    for order_id in stale_orders:
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().filter(pk=order_id).first()
                if order and order.status == OrderStatus.PENDING and order.expires_at and timezone.now() >= order.expires_at:
                    OrderService.expire_pending_order(order)
                    count += 1
        except Exception as exc:
            logger.exception("Failed to sweep stale order %s: %s", order_id, exc)

    if count:
        logger.info("Swept and expired %d stale pending order(s).", count)
