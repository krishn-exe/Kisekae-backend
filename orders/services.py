from decimal import Decimal
import logging
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import StockChangeReason, StockUpdate
from products.models import Product, ProductSize, ProductVariant, ProductCategory
from stores.models import StoreStatus

from .models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    PaymentStatus,
)
from .razorpay_service import RazorpayService

logger = logging.getLogger(__name__)


class OrderService:
    @staticmethod
    @transaction.atomic
    def create_order(store, customer_data, items_data, user=None):
        """
        Creates an order with 5-minute inventory reservation and generates
        a Razorpay order payload for payment capture.
        """
        if not user:
            raise ValidationError("A valid user account is required to place an order.")

        if store.status != StoreStatus.ACTIVE:
            raise ValidationError(f"Cannot place order for a store that is {store.status.lower()}.")

        if not store.is_business_verified:
            raise ValidationError("Store business verification must be approved before orders can be placed.")

        order = Order.objects.create(
            user=user,
            store=store,
            customer_name=customer_data["customer_name"],
            customer_email=customer_data["customer_email"],
            customer_phone=customer_data["customer_phone"],
            shipping_address=customer_data["shipping_address"],
            notes=customer_data.get("notes", ""),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            expires_at=timezone.now() + timezone.timedelta(minutes=5),
        )

        total_amount = Decimal("0.00")

        for item_data in items_data:
            product_id = item_data["product_id"]
            qty = int(item_data.get("quantity", 1))
            if qty <= 0:
                raise ValidationError("Quantity must be greater than zero.")

            product = Product.objects.select_for_update().filter(store=store, pk=product_id).first()
            if not product:
                raise ValidationError(f"Product ID {product_id} not found in this store.")

            if not product.is_active:
                raise ValidationError(f"Product '{product.name}' is not currently active for purchase.")

            variant_id = item_data.get("variant_id")
            variant = None
            if variant_id:
                variant = product.variants.filter(pk=variant_id).first()
                if not variant:
                    raise ValidationError(f"Color variant ID {variant_id} not found for product '{product.name}'.")
            else:
                # Default to first active variant if exists
                variant = product.variants.filter(is_active=True).first()

            size_name = str(item_data.get("size", "")).strip().upper()
            size_obj = None

            # Look up size in the specific variant if variant exists
            if variant and variant.sizes.exists():
                if size_name:
                    size_obj = variant.sizes.select_for_update().filter(size=size_name).first()
                else:
                    # Pick size="" or first in-stock size if no sizes
                    size_obj = variant.sizes.select_for_update().filter(size="").first()
                    if not size_obj and variant.sizes.count() == 1:
                        size_obj = variant.sizes.select_for_update().first()
            elif product.sizes.exists():
                # Fallback to product sizes
                if size_name:
                    size_obj = product.sizes.select_for_update().filter(size=size_name).first()
                else:
                    size_obj = product.sizes.select_for_update().filter(size="").first()
                    if not size_obj and product.sizes.count() == 1:
                        size_obj = product.sizes.select_for_update().first()

            if size_obj:
                if size_obj.stock < qty:
                    color_desc = f" ({variant.color_name})" if variant else ""
                    size_desc = f" in size '{size_name}'" if size_name else ""
                    raise ValidationError(
                        f"Insufficient stock for '{product.name}'{color_desc}{size_desc}. Available: {size_obj.stock}, requested: {qty}."
                    )
                prev_size_stock = size_obj.stock
                new_size_stock = prev_size_stock - qty
                size_obj.stock = new_size_stock
                size_obj.save(update_fields=["stock", "updated_at"])

                # Exact audit logging (BUG-11 fix)
                StockUpdate.objects.create(
                    product=product,
                    variant=variant,
                    variant_size=size_obj,
                    previous_stock=prev_size_stock,
                    new_stock=new_size_stock,
                    change_amount=-qty,
                    reason=StockChangeReason.RESERVATION_HOLD,
                    notes=f"5-min reservation hold for Order {order.order_number}",
                    created_by=user,
                )
                product.sync_total_stock()
            else:
                # General product stock fallback if no size objects exist
                if product.stock < qty:
                    raise ValidationError(
                        f"Insufficient stock for '{product.name}'. Available: {product.stock}, requested: {qty}."
                    )
                prev_stock = product.stock
                new_stock = prev_stock - qty
                product.stock = new_stock
                product.save(update_fields=["stock", "updated_at"])

                StockUpdate.objects.create(
                    product=product,
                    variant=variant,
                    variant_size=None,
                    previous_stock=prev_stock,
                    new_stock=new_stock,
                    change_amount=-qty,
                    reason=StockChangeReason.RESERVATION_HOLD,
                    notes=f"5-min reservation hold for Order {order.order_number}",
                    created_by=user,
                )

            item_total = product.price * qty
            total_amount += item_total

            OrderItem.objects.create(
                order=order,
                product=product,
                variant=variant,
                variant_size=size_obj,
                product_name=product.name,
                product_sku=variant.sku if (variant and variant.sku) else product.sku,
                color_name=variant.color_name if variant else (product.color or ""),
                color_hex=variant.color_hex if variant else "#000000",
                size=size_name,
                unit_price=product.price,
                quantity=qty,
                total_price=item_total,
            )

        order.total_amount = total_amount
        order.save(update_fields=["total_amount", "updated_at"])

        # Create order in Razorpay
        rzp_order = RazorpayService.create_order(
            amount_in_inr=total_amount,
            currency=order.currency,
            receipt=order.order_number,
            notes={"order_id": order.id, "store_id": store.id},
        )
        order.razorpay_order_id = rzp_order.get("id", "")
        order.save(update_fields=["razorpay_order_id", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status="",
            to_status=OrderStatus.PENDING,
            changed_by=user,
            notes="Order placed with 5-minute stock reservation. Awaiting Razorpay payment.",
        )

        # Enqueue 5-minute expiration task
        from .tasks import expire_pending_order_task
        order_id_val = order.id
        transaction.on_commit(lambda: expire_pending_order_task.apply_async(args=[order_id_val], countdown=300))

        return order

    @staticmethod
    @transaction.atomic
    def verify_payment(order_id, razorpay_payment_id, razorpay_order_id, razorpay_signature, user=None):
        """
        Verifies Razorpay HMAC SHA256 payment signature and advances order to CONFIRMED.
        If order expired, automatically refunds the captured payment.
        """
        order = Order.objects.select_for_update().filter(pk=order_id).first()
        if not order:
            raise ValidationError("Order not found.", code="ORDER_NOT_FOUND")

        # Signature verification
        is_valid = RazorpayService.verify_payment_signature(
            razorpay_order_id=razorpay_order_id or order.razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        )
        if not is_valid:
            raise ValidationError("Invalid payment signature from payment gateway.", code="INVALID_SIGNATURE")

        now = timezone.now()
        # Check if 5-minute TTL elapsed
        if order.expires_at and now > order.expires_at and order.status != OrderStatus.CONFIRMED:
            # Order expired: refund payment and fail
            logger.warning("Payment received after 5-minute window for order %s. Initiating auto-refund.", order.order_number)
            refund_data = RazorpayService.initiate_refund(
                razorpay_payment_id=razorpay_payment_id,
                amount_in_inr=order.total_amount,
                notes={"reason": "Reservation expired before payment completion"},
            )
            order.status = OrderStatus.FAILED
            order.payment_status = PaymentStatus.REFUNDED
            order.refund_id = refund_data.get("id", "")
            order.refund_amount = order.total_amount
            order.refund_reason = "Payment captured after 5-minute reservation expired."
            order.save(update_fields=["status", "payment_status", "refund_id", "refund_amount", "refund_reason", "updated_at"])

            OrderStatusHistory.objects.create(
                order=order,
                from_status=OrderStatus.PENDING,
                to_status=OrderStatus.FAILED,
                changed_by=user,
                notes="Payment completed after 5-minute reservation expired. Automatic refund issued.",
            )
            raise ValidationError(
                "Payment window expired (5 minutes). Your payment has been automatically refunded.",
                code="ORDER_EXPIRED",
            )

        if order.status == OrderStatus.CONFIRMED:
            return order

        old_status = order.status
        order.status = OrderStatus.CONFIRMED
        order.payment_status = PaymentStatus.PAID
        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_order_id = razorpay_order_id or order.razorpay_order_id
        order.razorpay_signature = razorpay_signature
        order.save(update_fields=[
            "status",
            "payment_status",
            "razorpay_payment_id",
            "razorpay_order_id",
            "razorpay_signature",
            "updated_at",
        ])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=OrderStatus.CONFIRMED,
            changed_by=user,
            notes=f"Payment verified successfully (ID: {razorpay_payment_id}).",
        )

        from notifications.tasks import send_order_lifecycle_email_task, send_seller_new_order_alert_task
        order_id_val = order.id
        transaction.on_commit(lambda: send_order_lifecycle_email_task.delay(order_id_val, "CONFIRMED"))
        transaction.on_commit(lambda: send_seller_new_order_alert_task.delay(order_id_val))

        return order

    @staticmethod
    @transaction.atomic
    def expire_pending_order(order):
        """
        Releases reserved stock back to the inventory when an order's 5-minute
        payment window expires without successful payment.
        """
        if order.status != OrderStatus.PENDING:
            return order

        for item in order.items.select_related("product", "variant", "variant_size").all():
            size_obj = None
            if item.variant_size:
                size_obj = ProductSize.objects.select_for_update().filter(pk=item.variant_size.pk).first()
            elif item.size and item.product:
                size_obj = ProductSize.objects.select_for_update().filter(product=item.product, size=item.size).first()

            if size_obj:
                prev_stock = size_obj.stock
                new_stock = prev_stock + item.quantity
                size_obj.stock = new_stock
                size_obj.save(update_fields=["stock", "updated_at"])

                StockUpdate.objects.create(
                    product=item.product,
                    variant=item.variant,
                    variant_size=size_obj,
                    previous_stock=prev_stock,
                    new_stock=new_stock,
                    change_amount=item.quantity,
                    reason=StockChangeReason.RESERVATION_RELEASE,
                    notes=f"Expired reservation released for Order {order.order_number}",
                )
                item.product.sync_total_stock()
            elif item.product:
                prod = Product.objects.select_for_update().filter(pk=item.product.pk).first()
                if prod:
                    prev_stock = prod.stock
                    new_stock = prev_stock + item.quantity
                    prod.stock = new_stock
                    prod.save(update_fields=["stock", "updated_at"])

                    StockUpdate.objects.create(
                        product=prod,
                        variant=item.variant,
                        variant_size=None,
                        previous_stock=prev_stock,
                        new_stock=new_stock,
                        change_amount=item.quantity,
                        reason=StockChangeReason.RESERVATION_RELEASE,
                        notes=f"Expired reservation released for Order {order.order_number}",
                    )

        order.status = OrderStatus.FAILED
        order.payment_status = PaymentStatus.FAILED
        order.save(update_fields=["status", "payment_status", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=OrderStatus.PENDING,
            to_status=OrderStatus.FAILED,
            notes="5-minute payment reservation window expired.",
        )
        return order

    @staticmethod
    @transaction.atomic
    def cancel_order(order, user=None, reason=""):
        """
        Cancels an order if it has not yet shipped (CONFIRMED or PENDING).
        Issues full Razorpay refund and restores item inventory.
        """
        if order.status not in {OrderStatus.CONFIRMED, OrderStatus.PENDING}:
            raise ValidationError(
                f"Cannot cancel order in '{order.status}' status. Only unshipped orders can be cancelled.",
                code="ORDER_CANNOT_BE_CANCELLED",
            )

        old_status = order.status

        # Issue refund if payment was already captured
        if order.payment_status == PaymentStatus.PAID and order.razorpay_payment_id:
            refund_data = RazorpayService.initiate_refund(
                razorpay_payment_id=order.razorpay_payment_id,
                amount_in_inr=order.total_amount,
                notes={"order_number": order.order_number, "reason": reason or "Customer cancelled order"},
            )
            order.refund_id = refund_data.get("id", "")
            order.refund_amount = order.total_amount
            order.payment_status = PaymentStatus.REFUNDED

        # Restore inventory
        for item in order.items.select_related("product", "variant", "variant_size").all():
            size_obj = None
            if item.variant_size:
                size_obj = ProductSize.objects.select_for_update().filter(pk=item.variant_size.pk).first()
            elif item.size and item.product:
                size_obj = ProductSize.objects.select_for_update().filter(product=item.product, size=item.size).first()

            if size_obj:
                prev_stock = size_obj.stock
                new_stock = prev_stock + item.quantity
                size_obj.stock = new_stock
                size_obj.save(update_fields=["stock", "updated_at"])

                StockUpdate.objects.create(
                    product=item.product,
                    variant=item.variant,
                    variant_size=size_obj,
                    previous_stock=prev_stock,
                    new_stock=new_stock,
                    change_amount=item.quantity,
                    reason=StockChangeReason.ORDER_CANCELLED,
                    notes=f"Restored from cancelled Order {order.order_number}",
                    created_by=user,
                )
                item.product.sync_total_stock()
            elif item.product:
                prod = Product.objects.select_for_update().filter(pk=item.product.pk).first()
                if prod:
                    prev_stock = prod.stock
                    new_stock = prev_stock + item.quantity
                    prod.stock = new_stock
                    prod.save(update_fields=["stock", "updated_at"])

                    StockUpdate.objects.create(
                        product=prod,
                        variant=item.variant,
                        variant_size=None,
                        previous_stock=prev_stock,
                        new_stock=new_stock,
                        change_amount=item.quantity,
                        reason=StockChangeReason.ORDER_CANCELLED,
                        notes=f"Restored from cancelled Order {order.order_number}",
                        created_by=user,
                    )

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = timezone.now()
        order.refund_reason = reason or "Cancelled by user"
        order.save(update_fields=[
            "status",
            "payment_status",
            "refund_id",
            "refund_amount",
            "refund_reason",
            "cancelled_at",
            "updated_at",
        ])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=OrderStatus.CANCELLED,
            changed_by=user,
            notes=reason or "Order cancelled.",
        )

        from notifications.tasks import send_order_lifecycle_email_task
        order_id_val = order.id
        transaction.on_commit(lambda: send_order_lifecycle_email_task.delay(order_id_val, "CANCELLED", reason))

        return order

    @staticmethod
    @transaction.atomic
    def request_refund(order, user=None, reason=""):
        """
        Buyer requests a refund/return post-delivery.
        Validates the strict 10-day return window and seller is_refundable policy.
        """
        is_eligible, err_msg = order.check_refund_eligibility()
        if not is_eligible:
            raise ValidationError(err_msg, code="REFUND_NOT_ELIGIBLE")

        old_status = order.status
        order.status = OrderStatus.REFUND_REQUESTED
        order.refund_reason = reason or "Customer requested return / refund."
        order.save(update_fields=["status", "refund_reason", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=OrderStatus.REFUND_REQUESTED,
            changed_by=user,
            notes=reason or "Customer return and refund requested.",
        )
        return order

    @staticmethod
    @transaction.atomic
    def approve_refund(order, actor_user=None, notes=""):
        """
        Store owner/manager approves refund: initiates Razorpay refund and
        restores the variant size inventory.
        """
        if order.status != OrderStatus.REFUND_REQUESTED:
            raise ValidationError(
                f"Cannot approve refund for order in '{order.status}' status (must be REFUND_REQUESTED).",
                code="INVALID_STATUS",
            )

        if order.payment_status == PaymentStatus.PAID and order.razorpay_payment_id:
            refund_data = RazorpayService.initiate_refund(
                razorpay_payment_id=order.razorpay_payment_id,
                amount_in_inr=order.total_amount,
                notes={"order_number": order.order_number, "reason": notes or "Refund request approved"},
            )
            order.refund_id = refund_data.get("id", "")
            order.refund_amount = order.total_amount
            order.payment_status = PaymentStatus.REFUNDED

        # Restore inventory
        for item in order.items.select_related("product", "variant", "variant_size").all():
            size_obj = None
            if item.variant_size:
                size_obj = ProductSize.objects.select_for_update().filter(pk=item.variant_size.pk).first()
            elif item.size and item.product:
                size_obj = ProductSize.objects.select_for_update().filter(product=item.product, size=item.size).first()

            if size_obj:
                prev_stock = size_obj.stock
                new_stock = prev_stock + item.quantity
                size_obj.stock = new_stock
                size_obj.save(update_fields=["stock", "updated_at"])

                StockUpdate.objects.create(
                    product=item.product,
                    variant=item.variant,
                    variant_size=size_obj,
                    previous_stock=prev_stock,
                    new_stock=new_stock,
                    change_amount=item.quantity,
                    reason=StockChangeReason.REFUND_RESTOCK,
                    notes=f"Restocked from approved refund for Order {order.order_number}",
                    created_by=actor_user,
                )
                item.product.sync_total_stock()
            elif item.product:
                prod = Product.objects.select_for_update().filter(pk=item.product.pk).first()
                if prod:
                    prev_stock = prod.stock
                    new_stock = prev_stock + item.quantity
                    prod.stock = new_stock
                    prod.save(update_fields=["stock", "updated_at"])

                    StockUpdate.objects.create(
                        product=prod,
                        variant=item.variant,
                        variant_size=None,
                        previous_stock=prev_stock,
                        new_stock=new_stock,
                        change_amount=item.quantity,
                        reason=StockChangeReason.REFUND_RESTOCK,
                        notes=f"Restocked from approved refund for Order {order.order_number}",
                        created_by=actor_user,
                    )

        old_status = order.status
        order.status = OrderStatus.REFUNDED
        order.refunded_at = timezone.now()
        order.save(update_fields=[
            "status",
            "payment_status",
            "refund_id",
            "refund_amount",
            "refunded_at",
            "updated_at",
        ])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=OrderStatus.REFUNDED,
            changed_by=actor_user,
            notes=notes or "Refund approved and processed via Razorpay.",
        )
        return order

    @staticmethod
    @transaction.atomic
    def reject_refund(order, actor_user=None, notes=""):
        """
        Store owner/manager rejects refund request: reverts order status back to DELIVERED.
        """
        if order.status != OrderStatus.REFUND_REQUESTED:
            raise ValidationError(
                f"Cannot reject refund for order in '{order.status}' status (must be REFUND_REQUESTED).",
                code="INVALID_STATUS",
            )

        old_status = order.status
        order.status = OrderStatus.DELIVERED
        order.save(update_fields=["status", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=OrderStatus.DELIVERED,
            changed_by=actor_user,
            notes=notes or "Refund request rejected by seller.",
        )
        return order

    @staticmethod
    @transaction.atomic
    def update_order_status(order, new_status, user=None, notes="", tracking_number="", tracking_url=""):
        if order.status == new_status:
            return order

        if not order.can_transition_to(new_status):
            raise ValidationError(
                f"Cannot transition order status from '{order.status}' to '{new_status}'."
            )

        old_status = order.status
        update_fields = ["status", "updated_at"]

        if new_status == OrderStatus.DELIVERED and not order.delivered_at:
            order.delivered_at = timezone.now()
            update_fields.append("delivered_at")

        if tracking_number:
            order.tracking_number = tracking_number
            update_fields.append("tracking_number")
        if tracking_url:
            order.tracking_url = tracking_url
            update_fields.append("tracking_url")

        order.status = new_status
        order.save(update_fields=update_fields)

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=new_status,
            changed_by=user,
            notes=notes or f"Status changed from {old_status} to {new_status}.",
        )

        if new_status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED}:
            from notifications.tasks import send_order_lifecycle_email_task
            order_id_val = order.id
            status_val = new_status
            notes_val = notes or ""
            transaction.on_commit(lambda: send_order_lifecycle_email_task.delay(order_id_val, status_val, notes_val))

        return order

    @staticmethod
    @transaction.atomic
    def mark_order_delivered(order, user=None, notes=""):
        return OrderService.update_order_status(
            order=order,
            new_status=OrderStatus.DELIVERED,
            user=user,
            notes=notes,
        )
