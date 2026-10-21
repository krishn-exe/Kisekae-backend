from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from products.models import Product

from .models import StockAlert, StockChangeReason, StockUpdate


class InventoryService:
    @staticmethod
    @transaction.atomic
    def update_stock(
        product,
        new_stock=None,
        adjustment=None,
        reason=StockChangeReason.MANUAL_ADJUSTMENT,
        notes="",
        user=None,
    ):
        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        previous_stock = locked_product.stock

        if adjustment is not None:
            computed_stock = previous_stock + adjustment
            if computed_stock < 0:
                raise ValidationError(
                    f"Adjustment of {adjustment} would reduce stock below zero (current: {previous_stock})."
                )
            target_stock = computed_stock
            change_amount = adjustment
        elif new_stock is not None:
            if new_stock < 0:
                raise ValidationError("Stock cannot be negative.")
            target_stock = new_stock
            change_amount = new_stock - previous_stock
        else:
            raise ValidationError("Either new_stock or adjustment must be specified.")

        locked_product.stock = target_stock
        locked_product.save(update_fields=["stock", "updated_at"])

        update_record = StockUpdate.objects.create(
            product=locked_product,
            previous_stock=previous_stock,
            new_stock=target_stock,
            change_amount=change_amount,
            reason=reason,
            notes=notes,
            created_by=user,
        )

        # Check and trigger stock alert if applicable
        alert = StockAlert.objects.filter(product=locked_product, is_active=True).first()
        if alert and target_stock <= alert.threshold:
            alert.last_triggered_at = timezone.now()
            alert.save(update_fields=["last_triggered_at", "updated_at"])
            from notifications.tasks import send_seller_low_stock_alert_task
            prod_id_val = locked_product.id
            curr_stock_val = target_stock
            thresh_val = alert.threshold
            transaction.on_commit(lambda: send_seller_low_stock_alert_task.delay(prod_id_val, current_stock=curr_stock_val, threshold=thresh_val))

        return locked_product, update_record

    @staticmethod
    @transaction.atomic
    def bulk_update_stock(store, items_data, user=None):
        results = []
        for item in items_data:
            product_id = item["product_id"]
            product = Product.objects.filter(store=store, pk=product_id).first()
            if not product:
                raise ValidationError(f"Product with ID {product_id} does not exist in this store.")

            updated_product, update_record = InventoryService.update_stock(
                product=product,
                new_stock=item.get("stock"),
                adjustment=item.get("adjustment"),
                reason=item.get("reason", StockChangeReason.MANUAL_ADJUSTMENT),
                notes=item.get("notes", ""),
                user=user,
            )
            results.append((updated_product, update_record))

        return results

    @staticmethod
    def set_alert(product, threshold=5, is_active=True):
        if threshold < 0:
            raise ValidationError("Threshold cannot be negative.")
        alert, _ = StockAlert.objects.update_or_create(
            product=product,
            defaults={"threshold": threshold, "is_active": is_active},
        )
        return alert

    @staticmethod
    def get_low_stock_products(store):
        """
        Returns all products in the store whose current stock is at or below
        their configured alert threshold, or at or below default threshold (5).
        """
        products = Product.objects.filter(store=store).select_related("stock_alert")
        low_stock = []
        for p in products:
            threshold = p.stock_alert.threshold if hasattr(p, "stock_alert") and p.stock_alert.is_active else 5
            if p.stock <= threshold:
                low_stock.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "sku": p.sku,
                    "current_stock": p.stock,
                    "threshold": threshold,
                    "status": p.status,
                })
        return low_stock
