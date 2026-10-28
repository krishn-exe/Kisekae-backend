import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_order_lifecycle_email_task(order_id: int, event_type: str, notes: str = ""):
    """
    Asynchronous task to deliver customer order lifecycle emails.
    """
    from orders.models import Order
    from .email_service import NotificationEmailService

    order = (
        Order.objects.filter(pk=order_id)
        .select_related("user", "store")
        .prefetch_related("items")
        .first()
    )
    if not order:
        logger.warning("send_order_lifecycle_email_task: Order %d not found.", order_id)
        return False

    return NotificationEmailService.send_order_lifecycle_email(order=order, event_type=event_type, notes=notes)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_seller_new_order_alert_task(order_id: int):
    """
    Asynchronous task to deliver new order alerts to Store Owners and Managers.
    """
    from orders.models import Order
    from .email_service import NotificationEmailService

    order = (
        Order.objects.filter(pk=order_id)
        .select_related("store")
        .prefetch_related("items")
        .first()
    )
    if not order:
        logger.warning("send_seller_new_order_alert_task: Order %d not found.", order_id)
        return 0

    return NotificationEmailService.send_seller_new_order_alert(order=order)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_seller_return_requested_alert_task(order_id: int):
    """
    Asynchronous task to deliver return requested alerts to Store Owners and Managers.
    """
    from orders.models import Order
    from .email_service import NotificationEmailService

    order = (
        Order.objects.filter(pk=order_id)
        .select_related("store")
        .prefetch_related("items")
        .first()
    )
    if not order:
        logger.warning("send_seller_return_requested_alert_task: Order %d not found.", order_id)
        return 0

    return NotificationEmailService.send_seller_return_requested_alert(order=order)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_store_verification_submitted_email_task(verification_id: int):
    """
    Asynchronous task to deliver verification submission receipt to Store Owner.
    """
    from stores.models import BusinessVerification
    from .email_service import NotificationEmailService

    verification = (
        BusinessVerification.objects.filter(pk=verification_id)
        .select_related("store", "store__created_by")
        .first()
    )
    if not verification:
        logger.warning("send_store_verification_submitted_email_task: Verification %d not found.", verification_id)
        return False

    return NotificationEmailService.send_store_verification_submitted_email(verification=verification)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_store_verification_result_email_task(verification_id: int):
    """
    Asynchronous task to deliver verification outcome (Approved/Rejected) to Store Owner.
    """
    from stores.models import BusinessVerification
    from .email_service import NotificationEmailService

    verification = (
        BusinessVerification.objects.filter(pk=verification_id)
        .select_related("store", "store__created_by")
        .first()
    )
    if not verification:
        logger.warning("send_store_verification_result_email_task: Verification %d not found.", verification_id)
        return False

    return NotificationEmailService.send_store_verification_result_email(verification=verification)


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_seller_low_stock_total_alert_task(product_id: int, current_stock: int):
    """
    Asynchronous task to deliver total low stock alerts (< 250 units) to Store Owners and Managers.
    """
    from products.models import Product
    from .email_service import NotificationEmailService

    product = Product.objects.filter(pk=product_id).select_related("store").first()
    if not product:
        logger.warning("send_seller_low_stock_total_alert_task: Product %d not found.", product_id)
        return 0

    return NotificationEmailService.send_seller_low_stock_total_alert(
        product=product,
        current_stock=current_stock,
    )


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_seller_low_stock_variant_alert_task(product_id: int, size: str, current_stock: int):
    """
    Asynchronous task to deliver variant size low stock alerts (< 20 units) to Store Owners and Managers.
    """
    from products.models import Product
    from .email_service import NotificationEmailService

    product = Product.objects.filter(pk=product_id).select_related("store").first()
    if not product:
        logger.warning("send_seller_low_stock_variant_alert_task: Product %d not found.", product_id)
        return 0

    return NotificationEmailService.send_seller_low_stock_variant_alert(
        product=product,
        size=size,
        current_stock=current_stock,
    )


@shared_task(autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_seller_low_stock_alert_task(product_id: int, size: str = "", current_stock: int = 0, threshold: int = 5):
    """
    Legacy backwards-compatible task for low stock notifications.
    """
    from products.models import Product
    from .email_service import NotificationEmailService

    product = Product.objects.filter(pk=product_id).select_related("store").first()
    if not product:
        logger.warning("send_seller_low_stock_alert_task: Product %d not found.", product_id)
        return 0

    return NotificationEmailService.send_seller_low_stock_alert(
        product=product,
        size=size,
        current_stock=current_stock,
        threshold=threshold,
    )
