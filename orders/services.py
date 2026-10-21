from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import StockChangeReason
from inventory.services import InventoryService
from products.models import Product
from stores.models import StoreStatus

from .models import (
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
)


class OrderService:
    @staticmethod
    @transaction.atomic
    def create_order(store, customer_data, items_data, user=None):
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
        )

        total_amount = Decimal("0.00")

        for item_data in items_data:
            product_id = item_data["product_id"]
            qty = item_data["quantity"]

            product = Product.objects.select_for_update().filter(store=store, pk=product_id).first()
            if not product:
                raise ValidationError(f"Product ID {product_id} not found in this store.")

            if not product.is_active:
                raise ValidationError(f"Product '{product.name}' is not currently active for purchase.")

            size = str(item_data.get("size", "")).strip().upper()

            if size and product.sizes.exists():
                size_obj = product.sizes.select_for_update().filter(size=size).first()
                if not size_obj:
                    valid_sizes = list(product.sizes.values_list("size", flat=True))
                    raise ValidationError(
                        f"Size '{size}' not found for product '{product.name}'. Available sizes: {', '.join(valid_sizes)}."
                    )
                if size_obj.stock < qty:
                    raise ValidationError(
                        f"Insufficient stock for '{product.name}' in size '{size}'. Available: {size_obj.stock}, requested: {qty}."
                    )
                size_obj.stock -= qty
                size_obj.save(update_fields=["stock", "updated_at"])
                product.sync_total_stock()
                # Create stock update log
                InventoryService.update_stock(
                    product=product,
                    new_stock=product.stock,
                    reason=StockChangeReason.ORDER_PLACED,
                    notes=f"Order {order.order_number} (Size {size})",
                    user=user,
                )
            else:
                if product.stock < qty:
                    raise ValidationError(
                        f"Insufficient stock for '{product.name}'. Available: {product.stock}, requested: {qty}."
                    )
                # Auto-decrement stock via InventoryService
                InventoryService.update_stock(
                    product=product,
                    adjustment=-qty,
                    reason=StockChangeReason.ORDER_PLACED,
                    notes=f"Order {order.order_number}",
                    user=user,
                )

            item_total = product.price * qty
            total_amount += item_total

            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                product_sku=product.sku,
                size=size,
                unit_price=product.price,
                quantity=qty,
                total_price=item_total,
            )

        order.total_amount = total_amount
        order.save(update_fields=["total_amount", "updated_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status="",
            to_status=OrderStatus.PENDING,
            changed_by=user,
            notes="Order placed successfully.",
        )

        from notifications.tasks import send_order_lifecycle_email_task, send_seller_new_order_alert_task
        order_id_val = order.id
        transaction.on_commit(lambda: send_order_lifecycle_email_task.delay(order_id_val, "CONFIRMED"))
        transaction.on_commit(lambda: send_seller_new_order_alert_task.delay(order_id_val))

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

        # If order is cancelled, restore stock (including size-level stock if applicable)
        if new_status == OrderStatus.CANCELLED:
            for item in order.items.select_related("product").all():
                if item.product:
                    size_name = (item.size or "").strip().upper()
                    if size_name and item.product.sizes.exists():
                        size_obj = item.product.sizes.filter(size=size_name).first()
                        if size_obj:
                            size_obj.stock += item.quantity
                            size_obj.save(update_fields=["stock", "updated_at"])
                            item.product.sync_total_stock()
                            InventoryService.update_stock(
                                product=item.product,
                                new_stock=item.product.stock,
                                reason=StockChangeReason.ORDER_CANCELLED,
                                notes=f"Restored from cancelled order {order.order_number} (Size {size_name})",
                                user=user,
                            )
                            continue
                    InventoryService.update_stock(
                        product=item.product,
                        adjustment=item.quantity,
                        reason=StockChangeReason.ORDER_CANCELLED,
                        notes=f"Restored from cancelled order {order.order_number}",
                        user=user,
                    )

        order.status = new_status
        order.save(update_fields=update_fields)

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status=new_status,
            changed_by=user,
            notes=notes or f"Status changed from {old_status} to {new_status}.",
        )

        if new_status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.CANCELLED}:
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
            notes=notes or "Order marked as delivered.",
        )
