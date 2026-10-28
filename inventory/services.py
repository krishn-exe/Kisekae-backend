from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Value, When
from django.utils import timezone

from products.models import Product, ProductSize, ProductVariant
from .models import StockAlert, StockChangeReason, StockUpdate


class InventoryService:
    @staticmethod
    @transaction.atomic
    def add_stock(
        product,
        delta,
        variant_id=None,
        size=None,
        reason=StockChangeReason.RESTOCK,
        notes="",
        user=None,
    ):
        """
        Atomically adds stock units (+delta) to a product or specific variant size
        under a select_for_update() row lock, preventing race conditions.
        """
        if delta <= 0:
            raise ValidationError("Stock delta to add must be a positive integer greater than zero.")

        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        variant = None
        size_obj = None

        if variant_id:
            variant = locked_product.variants.filter(pk=variant_id).first()
            if not variant:
                raise ValidationError(f"Variant ID {variant_id} not found for this product.")
        else:
            variant = locked_product.variants.first()

        clean_size = (size or "").strip().upper()
        if variant and variant.sizes.exists():
            if clean_size:
                size_obj = variant.sizes.select_for_update().filter(size=clean_size).first()
            else:
                size_obj = variant.sizes.select_for_update().filter(size="").first()
                if not size_obj and variant.sizes.count() == 1:
                    size_obj = variant.sizes.select_for_update().first()
        elif locked_product.sizes.exists():
            if clean_size:
                size_obj = locked_product.sizes.select_for_update().filter(size=clean_size).first()
            else:
                size_obj = locked_product.sizes.select_for_update().filter(size="").first()
                if not size_obj and locked_product.sizes.count() == 1:
                    size_obj = locked_product.sizes.select_for_update().first()

        if size_obj:
            prev_stock = size_obj.stock
            new_stock = prev_stock + delta
            size_obj.stock = new_stock
            size_obj.save(update_fields=["stock", "updated_at"])

            update_record = StockUpdate.objects.create(
                product=locked_product,
                variant=variant,
                variant_size=size_obj,
                previous_stock=prev_stock,
                new_stock=new_stock,
                change_amount=delta,
                reason=reason,
                notes=notes,
                created_by=user,
            )
            locked_product.sync_total_stock()
            return locked_product, update_record

        # General product stock addition
        prev_stock = locked_product.stock
        new_stock = prev_stock + delta
        locked_product.stock = new_stock
        locked_product.save(update_fields=["stock", "updated_at"])

        update_record = StockUpdate.objects.create(
            product=locked_product,
            variant=variant,
            variant_size=None,
            previous_stock=prev_stock,
            new_stock=new_stock,
            change_amount=delta,
            reason=reason,
            notes=notes,
            created_by=user,
        )
        return locked_product, update_record

    @staticmethod
    @transaction.atomic
    def remove_stock(
        product,
        delta,
        variant_id=None,
        size=None,
        reason=StockChangeReason.MANUAL_ADJUSTMENT,
        notes="",
        user=None,
    ):
        """
        Atomically removes stock units (-delta) from a product or specific variant size
        under a select_for_update() row lock, verifying sufficient stock exists.
        """
        if delta <= 0:
            raise ValidationError("Stock delta to remove must be a positive integer greater than zero.")

        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        variant = None
        size_obj = None

        if variant_id:
            variant = locked_product.variants.filter(pk=variant_id).first()
            if not variant:
                raise ValidationError(f"Variant ID {variant_id} not found for this product.")
        else:
            variant = locked_product.variants.first()

        clean_size = (size or "").strip().upper()
        if variant and variant.sizes.exists():
            if clean_size:
                size_obj = variant.sizes.select_for_update().filter(size=clean_size).first()
            else:
                size_obj = variant.sizes.select_for_update().filter(size="").first()
                if not size_obj and variant.sizes.count() == 1:
                    size_obj = variant.sizes.select_for_update().first()
        elif locked_product.sizes.exists():
            if clean_size:
                size_obj = locked_product.sizes.select_for_update().filter(size=clean_size).first()
            else:
                size_obj = locked_product.sizes.select_for_update().filter(size="").first()
                if not size_obj and locked_product.sizes.count() == 1:
                    size_obj = locked_product.sizes.select_for_update().first()

        if size_obj:
            if size_obj.stock < delta:
                raise ValidationError(
                    f"Cannot remove {delta} units. Available stock is only {size_obj.stock}."
                )
            prev_stock = size_obj.stock
            new_stock = prev_stock - delta
            size_obj.stock = new_stock
            size_obj.save(update_fields=["stock", "updated_at"])

            update_record = StockUpdate.objects.create(
                product=locked_product,
                variant=variant,
                variant_size=size_obj,
                previous_stock=prev_stock,
                new_stock=new_stock,
                change_amount=-delta,
                reason=reason,
                notes=notes,
                created_by=user,
            )
            locked_product.sync_total_stock()
            InventoryService._check_stock_alert(locked_product, prev_stock=prev_stock, new_stock=new_stock, size=clean_size)
            return locked_product, update_record

        if locked_product.stock < delta:
            raise ValidationError(
                f"Cannot remove {delta} units. Available product stock is only {locked_product.stock}."
            )
        prev_stock = locked_product.stock
        new_stock = prev_stock - delta
        locked_product.stock = new_stock
        locked_product.save(update_fields=["stock", "updated_at"])

        update_record = StockUpdate.objects.create(
            product=locked_product,
            variant=variant,
            variant_size=None,
            previous_stock=prev_stock,
            new_stock=new_stock,
            change_amount=-delta,
            reason=reason,
            notes=notes,
            created_by=user,
        )
        InventoryService._check_stock_alert(locked_product, prev_stock=prev_stock, new_stock=new_stock)
        return locked_product, update_record

    @staticmethod
    @transaction.atomic
    def bulk_delta_stock(store, items_data, user=None):
        """
        Applies atomic positive or negative stock deltas across multiple products/variants.
        """
        results = []
        for item in items_data:
            product_id = item["product_id"]
            product = Product.objects.filter(store=store, pk=product_id).first()
            if not product:
                raise ValidationError(f"Product ID {product_id} not found in this store.")

            delta = item.get("delta", 0)
            if delta > 0:
                prod, update_rec = InventoryService.add_stock(
                    product=product,
                    delta=delta,
                    variant_id=item.get("variant_id"),
                    size=item.get("size"),
                    reason=item.get("reason", StockChangeReason.RESTOCK),
                    notes=item.get("notes", ""),
                    user=user,
                )
            elif delta < 0:
                prod, update_rec = InventoryService.remove_stock(
                    product=product,
                    delta=abs(delta),
                    variant_id=item.get("variant_id"),
                    size=item.get("size"),
                    reason=item.get("reason", StockChangeReason.MANUAL_ADJUSTMENT),
                    notes=item.get("notes", ""),
                    user=user,
                )
            else:
                continue

            results.append((prod, update_rec))
        return results

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
        """
        Maintained for backwards compatibility: sets absolute stock or signed adjustment.
        """
        if adjustment is not None:
            if adjustment >= 0:
                return InventoryService.add_stock(
                    product=product,
                    delta=adjustment,
                    reason=reason,
                    notes=notes,
                    user=user,
                )
            return InventoryService.remove_stock(
                product=product,
                delta=abs(adjustment),
                reason=reason,
                notes=notes,
                user=user,
            )

        if new_stock is None or new_stock < 0:
            raise ValidationError("Valid non-negative stock count must be provided.")

        locked_product = Product.objects.select_for_update().get(pk=product.pk)
        prev_stock = locked_product.stock
        delta = new_stock - prev_stock

        if locked_product.sizes.exists():
            # If product has sizes, adjust the primary size
            primary_size = locked_product.sizes.select_for_update().first()
            if primary_size:
                primary_size.stock = max(0, primary_size.stock + delta)
                primary_size.save(update_fields=["stock", "updated_at"])
                locked_product.sync_total_stock()

        locked_product.stock = new_stock
        locked_product.save(update_fields=["stock", "updated_at"])

        update_record = StockUpdate.objects.create(
            product=locked_product,
            previous_stock=prev_stock,
            new_stock=new_stock,
            change_amount=delta,
            reason=reason,
            notes=notes,
            created_by=user,
        )
        InventoryService._check_stock_alert(locked_product, prev_stock=prev_stock, new_stock=new_stock)
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
    def _check_stock_alert(product, prev_stock, new_stock, size=""):
        """
        Debounced alert trigger: sends an alert when crossing the threshold downward.
        Falls back to default threshold (5) if no custom StockAlert row exists.
        """
        alert = StockAlert.objects.filter(product=product).first()
        if alert and not alert.is_active:
            return

        threshold = alert.threshold if alert else 5
        if prev_stock > threshold >= new_stock:
            if alert:
                alert.last_triggered_at = timezone.now()
                alert.save(update_fields=["last_triggered_at", "updated_at"])
            from notifications.tasks import send_seller_low_stock_alert_task
            prod_id_val = product.id
            curr_stock_val = new_stock
            thresh_val = threshold
            size_val = size or ""
            transaction.on_commit(
                lambda: send_seller_low_stock_alert_task.delay(
                    prod_id_val, size=size_val, current_stock=curr_stock_val, threshold=thresh_val
                )
            )

    @staticmethod
    def set_alert(product, threshold=5, is_active=True):
        if threshold < 0:
            raise ValidationError("Threshold cannot be negative.")
        alert, _ = StockAlert.objects.update_or_create(
            product=product,
            defaults={"threshold": threshold, "is_active": is_active},
        )
        # Immediately check if current stock is below the newly configured threshold
        if is_active and product.stock <= threshold:
            alert.last_triggered_at = timezone.now()
            alert.save(update_fields=["last_triggered_at", "updated_at"])
            from notifications.tasks import send_seller_low_stock_alert_task
            prod_id_val = product.id
            curr_stock_val = product.stock
            thresh_val = threshold
            transaction.on_commit(
                lambda: send_seller_low_stock_alert_task.delay(
                    prod_id_val, current_stock=curr_stock_val, threshold=thresh_val
                )
            )
        return alert

    @staticmethod
    def get_low_stock_products_queryset(store):
        """
        Scalable database SQL query replacing Python in-memory looping (BUG-17 fix).
        """
        return (
            Product.objects.filter(store=store)
            .select_related("stock_alert")
            .annotate(
                effective_threshold=Case(
                    When(stock_alert__isnull=False, stock_alert__is_active=True, then=F("stock_alert__threshold")),
                    default=Value(5),
                    output_field=IntegerField(),
                )
            )
            .filter(stock__lte=F("effective_threshold"))
            .order_by("stock", "name")
        )

    @staticmethod
    def get_low_stock_products(store):
        """
        Returns low-stock product list dictionary for reporting/analytics.
        """
        qs = InventoryService.get_low_stock_products_queryset(store)
        return [
            {
                "product_id": p.id,
                "product_name": p.name,
                "sku": p.sku,
                "current_stock": p.stock,
                "threshold": getattr(p, "effective_threshold", 5),
                "status": p.status,
            }
            for p in qs
        ]
