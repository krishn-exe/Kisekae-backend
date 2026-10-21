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
def send_return_status_email_task(return_req_id: int):
    """
    Asynchronous task to deliver return request status updates.
    """
    from orders.models import ReturnRequest
    from .email_service import NotificationEmailService

    return_req = (
        ReturnRequest.objects.filter(pk=return_req_id)
        .select_related("order", "order__user", "order__store", "item")
        .first()
    )
    if not return_req:
        logger.warning("send_return_status_email_task: ReturnRequest %d not found.", return_req_id)
        return False

    return NotificationEmailService.send_return_status_email(return_req=return_req)


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
def send_seller_low_stock_alert_task(product_id: int, size: str = "", current_stock: int = 0, threshold: int = 5):
    """
    Asynchronous task to deliver low stock warnings to Store Owners and Managers.
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
