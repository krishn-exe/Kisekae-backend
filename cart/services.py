import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Product, ProductStatus, ProductVariant
from stores.models import StoreStatus

from .models import Cart, CartItem


class CartService:
    @staticmethod
    def get_or_create_cart(user):
        if not user or not user.is_authenticated:
            raise ValidationError("Authentication required to access cart.")
        user_cart, _ = Cart.objects.get_or_create(user=user)
        return user_cart

    @staticmethod
    @transaction.atomic
    def merge_carts(source_cart, target_cart):
        for item in source_cart.items.select_related("product", "variant").all():
            existing = target_cart.items.filter(
                product=item.product,
                variant=item.variant,
                size=item.size,
            ).first()

            max_stock = item.product.stock
            if item.variant and item.variant.sizes.exists():
                size_obj = item.variant.sizes.filter(size=item.size).first()
                if size_obj:
                    max_stock = size_obj.stock
            elif item.size and item.product.sizes.exists():
                size_obj = item.product.sizes.filter(size=item.size).first()
                if size_obj:
                    max_stock = size_obj.stock

            if existing:
                combined_qty = min(existing.quantity + item.quantity, max_stock)
                existing.quantity = max(combined_qty, 1)
                existing.save(update_fields=["quantity", "updated_at"])
            else:
                CartItem.objects.create(
                    cart=target_cart,
                    product=item.product,
                    variant=item.variant,
                    size=item.size,
                    quantity=min(item.quantity, max_stock),
                )
        source_cart.delete()
        target_cart.save()

    @staticmethod
    @transaction.atomic
    def add_item(cart, product_id, variant_id=None, quantity=1, size=""):
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        product = (
            Product.objects.select_related("store")
            .prefetch_related("variants__sizes", "sizes")
            .filter(pk=product_id)
            .first()
        )
        if not product:
            raise ValidationError("Product not found.")

        if product.status != ProductStatus.ACTIVE:
            raise ValidationError("This product is currently inactive and cannot be added to cart.")

        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise ValidationError("This store is currently unavailable.")

        clean_size = str(size or "").strip().upper()

        # Resolve variant
        variant = None
        if variant_id:
            variant = product.variants.filter(pk=variant_id).first()
            if not variant:
                raise ValidationError("Selected color variant not found.")
            if not variant.is_active:
                raise ValidationError("Selected color variant is inactive.")
        else:
            # Default to first active variant if available
            variant = product.variants.filter(is_active=True).first()

        # Handle size selection and stock
        available_stock = product.stock
        if variant and variant.sizes.exists():
            if not clean_size:
                first_avail = variant.sizes.filter(stock__gt=0).first()
                if first_avail:
                    clean_size = first_avail.size
                    available_stock = first_avail.stock
                else:
                    raise ValidationError(f"This color ({variant.color_name}) is currently out of stock in all sizes.")
            else:
                size_obj = variant.sizes.filter(size=clean_size).first()
                if not size_obj:
                    valid_sizes = list(variant.sizes.values_list("size", flat=True))
                    raise ValidationError(
                        f"Size '{clean_size}' is not valid for color '{variant.color_name}'. Available sizes: {', '.join(valid_sizes)}."
                    )
                if size_obj.stock <= 0:
                    raise ValidationError(f"Size '{clean_size}' in color '{variant.color_name}' is currently out of stock.")
                available_stock = size_obj.stock
        elif product.sizes.exists():
            if not clean_size:
                first_avail = product.sizes.filter(stock__gt=0).first()
                if first_avail:
                    clean_size = first_avail.size
                    available_stock = first_avail.stock
                else:
                    raise ValidationError("This product is currently out of stock in all sizes.")
            else:
                size_obj = product.sizes.filter(size=clean_size).first()
                if not size_obj:
                    valid_sizes = list(product.sizes.values_list("size", flat=True))
                    raise ValidationError(
                        f"Size '{clean_size}' is not valid for this product. Available sizes: {', '.join(valid_sizes)}."
                    )
                if size_obj.stock <= 0:
                    raise ValidationError(f"Size '{clean_size}' is currently out of stock.")
                available_stock = size_obj.stock

        if available_stock <= 0:
            raise ValidationError("This product is currently out of stock.")

        cart_item = cart.items.filter(product=product, variant=variant, size=clean_size).first()
        current_quantity = cart_item.quantity if cart_item else 0
        target_quantity = current_quantity + quantity

        if target_quantity > available_stock:
            size_note = f" in size '{clean_size}'" if clean_size else ""
            color_note = f" ({variant.color_name})" if variant else ""
            raise ValidationError(
                f"Cannot add {quantity} item(s). Only {available_stock - current_quantity} more item(s) available{color_note}{size_note}."
            )

        if cart_item:
            cart_item.quantity = target_quantity
            cart_item.save(update_fields=["quantity", "updated_at"])
        else:
            cart_item = CartItem.objects.create(
                cart=cart,
                product=product,
                variant=variant,
                size=clean_size,
                quantity=target_quantity,
            )

        cart.save(update_fields=["updated_at"])
        if cart.user_id:
            from products.tasks import recompute_user_taste_task
            transaction.on_commit(lambda: recompute_user_taste_task.delay(cart.user_id))
        return cart_item

    @staticmethod
    @transaction.atomic
    def update_item_quantity(cart, item_id, quantity):
        cart_item = cart.items.select_related("product", "variant").filter(pk=item_id).first()
        if not cart_item:
            raise ValidationError("Item not found in cart.")

        if quantity <= 0:
            cart_item.delete()
            cart.save(update_fields=["updated_at"])
            return None

        product = cart_item.product
        variant = cart_item.variant
        max_stock = product.stock

        if variant and variant.sizes.exists():
            size_obj = variant.sizes.filter(size=cart_item.size).first()
            if size_obj:
                max_stock = size_obj.stock
        elif cart_item.size and product.sizes.exists():
            size_obj = product.sizes.filter(size=cart_item.size).first()
            if size_obj:
                max_stock = size_obj.stock

        if quantity > max_stock:
            size_note = f" in size '{cart_item.size}'" if cart_item.size else ""
            raise ValidationError(
                f"Cannot set quantity to {quantity}. Only {max_stock} item(s) available{size_note}."
            )

        cart_item.quantity = quantity
        cart_item.save(update_fields=["quantity", "updated_at"])
        cart.save(update_fields=["updated_at"])
        if cart.user_id:
            from products.tasks import recompute_user_taste_task
            transaction.on_commit(lambda: recompute_user_taste_task.delay(cart.user_id))
        return cart_item

    @staticmethod
    def remove_item(cart, item_id):
        cart_item = cart.items.filter(pk=item_id).first()
        if not cart_item:
            raise ValidationError("Item not found in cart.")
        cart_item.delete()
        cart.save(update_fields=["updated_at"])

    @staticmethod
    def clear_cart(cart):
        cart.items.all().delete()
        cart.save(update_fields=["updated_at"])

    @staticmethod
    def validate_cart(cart):
        issues = []
        is_valid = True

        for item in cart.items.select_related("product", "variant", "product__store").all():
            product = item.product
            store = product.store
            variant = item.variant

            if store.status != StoreStatus.ACTIVE or store.archived_at is not None:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "size": item.size,
                    "issue": "Store is unavailable.",
                    "code": "STORE_UNAVAILABLE",
                })
            elif product.status != ProductStatus.ACTIVE:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "size": item.size,
                    "issue": "Product is no longer active.",
                    "code": "PRODUCT_INACTIVE",
                })
            elif variant and not variant.is_active:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "variant_id": variant.id,
                    "color_name": variant.color_name,
                    "size": item.size,
                    "issue": f"Color variant '{variant.color_name}' is inactive.",
                    "code": "VARIANT_INACTIVE",
                })
            else:
                max_stock = product.stock
                if variant and variant.sizes.exists():
                    size_obj = variant.sizes.filter(size=item.size).first()
                    max_stock = size_obj.stock if size_obj else 0
                elif item.size and product.sizes.exists():
                    size_obj = product.sizes.filter(size=item.size).first()
                    max_stock = size_obj.stock if size_obj else 0

                if max_stock < item.quantity:
                    is_valid = False
                    size_str = f" in size '{item.size}'" if item.size else ""
                    color_str = f" ({variant.color_name})" if variant else ""
                    issues.append({
                        "item_id": item.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "size": item.size,
                        "issue": f"Requested {item.quantity}, but only {max_stock} available{color_str}{size_str}.",
                        "code": "INSUFFICIENT_STOCK",
                        "available_stock": max_stock,
                    })

        return {
            "is_valid": is_valid,
            "issues_count": len(issues),
            "issues": issues,
            "total_items": cart.total_items,
            "subtotal": float(cart.subtotal),
            "currency": cart.currency,
        }

    @staticmethod
    @transaction.atomic
    def checkout_cart(cart, user, customer_data):
        """
        Converts all items in the cart into one or more PENDING orders grouped by store,
        places a 5-minute inventory reservation on the exact variant sizes, and generates
        Razorpay order payloads for each store order.
        """
        if not user or not user.is_authenticated:
            raise ValidationError("An authenticated user account is required to complete checkout.")

        items = list(cart.items.select_related("product", "variant", "product__store").all())
        if not items:
            raise ValidationError("Cannot checkout an empty cart.")

        # Validate cart stock and store active status
        report = CartService.validate_cart(cart)
        if not report["is_valid"]:
            issue_msgs = [f"{iss['product_name']}: {iss['issue']}" for iss in report["issues"]]
            raise ValidationError(f"Cart cannot be checked out due to inventory issues: {'; '.join(issue_msgs)}")

        # Group items by store
        items_by_store = {}
        for item in items:
            store = item.product.store
            items_by_store.setdefault(store, []).append(item)

        created_orders = []
        from orders.services import OrderService

        for store, store_items in items_by_store.items():
            items_payload = [
                {
                    "product_id": item.product.id,
                    "variant_id": item.variant.id if item.variant else None,
                    "size": item.size,
                    "quantity": item.quantity,
                }
                for item in store_items
            ]

            order = OrderService.create_order(
                store=store,
                customer_data=customer_data,
                items_data=items_payload,
                user=user,
            )
            created_orders.append(order)

        # Clear items from cart upon successful reservation
        cart.items.all().delete()
        cart.save(update_fields=["updated_at"])

        return created_orders

    @staticmethod
    @transaction.atomic
    def reorder(order, user):
        """
        Re-adds items from an existing order back into the user's active cart.
        """
        user_cart = CartService.get_or_create_cart(user=user)
        added_count = 0
        skipped = []

        for item in order.items.select_related("product", "variant").all():
            product = item.product
            variant = item.variant
            size = item.size
            qty = item.quantity

            if not product or product.status != ProductStatus.ACTIVE or product.store.status != StoreStatus.ACTIVE:
                skipped.append({"product_name": item.product_name, "reason": "Product or store is no longer active."})
                continue

            if variant and not variant.is_active:
                skipped.append({"product_name": item.product_name, "reason": f"Color variant '{item.color_name}' is inactive."})
                continue

            try:
                CartService.add_item(
                    cart=user_cart,
                    product_id=product.id,
                    variant_id=variant.id if variant else None,
                    quantity=qty,
                    size=size,
                )
                added_count += 1
            except ValidationError as exc:
                skipped.append({"product_name": item.product_name, "reason": str(exc)})

        return {
            "reordered_items_count": added_count,
            "skipped_items": skipped,
            "cart": user_cart,
        }
