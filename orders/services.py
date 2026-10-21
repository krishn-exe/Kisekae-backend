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
    ReturnRequest,
    ReturnStatus,
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

        # If order is cancelled, restore stock
        if new_status == OrderStatus.CANCELLED:
            for item in order.items.select_related("product").all():
                if item.product:
                    InventoryService.update_stock(
                        product=item.product,
                        adjustment=item.quantity,
                        reason=StockChangeReason.ORDER_CANCELLED,
                        notes=f"Restored from cancelled order {order.order_number}",
                        user=user,
                    )

        # If order is returned, restock items
        if new_status == OrderStatus.RETURNED:
            for item in order.items.select_related("product").all():
                if item.product:
                    InventoryService.update_stock(
                        product=item.product,
                        adjustment=item.quantity,
                        reason=StockChangeReason.RETURN_RESTOCK,
                        notes=f"Restocked from returned order {order.order_number}",
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

    @staticmethod
    @transaction.atomic
    def create_return_request(order, item=None, reason="", customer_notes=""):
        if order.status not in {OrderStatus.DELIVERED, OrderStatus.SHIPPED}:
            raise ValidationError("Returns can only be requested for shipped or delivered orders.")

        return_req = ReturnRequest.objects.create(
            order=order,
            item=item,
            reason=reason,
            customer_notes=customer_notes,
            status=ReturnStatus.REQUESTED,
        )

        if order.status != OrderStatus.RETURN_REQUESTED:
            order.status = OrderStatus.RETURN_REQUESTED
            order.save(update_fields=["status", "updated_at"])

            OrderStatusHistory.objects.create(
                order=order,
                from_status=OrderStatus.DELIVERED,
                to_status=OrderStatus.RETURN_REQUESTED,
                notes=f"Return requested: {reason}",
            )

        return return_req

    @staticmethod
    @transaction.atomic
    def resolve_return_request(return_request, new_status, user=None, admin_notes=""):
        return_request.status = new_status
        return_request.admin_notes = admin_notes
        return_request.resolved_by = user
        return_request.resolved_at = timezone.now()
        return_request.save(update_fields=["status", "admin_notes", "resolved_by", "resolved_at"])

        if new_status == ReturnStatus.COMPLETED:
            order = return_request.order
            if return_request.item and return_request.item.product:
                InventoryService.update_stock(
                    product=return_request.item.product,
                    adjustment=return_request.item.quantity,
                    reason=StockChangeReason.RETURN_RESTOCK,
                    notes=f"Restocked return #{return_request.id} for order {order.order_number}",
                    user=user,
                )
            else:
                # Restocking of all items is handled by update_order_status
                OrderService.update_order_status(
                    order=order,
                    new_status=OrderStatus.RETURNED,
                    user=user,
                    notes="Return completed and items restocked.",
                )

        return return_request
