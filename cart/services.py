import uuid
from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Product, ProductSize, ProductStatus
from stores.models import StoreStatus

from .models import Cart, CartItem


class CartService:
    @staticmethod
    def get_or_create_cart(user=None, cart_token=None):
        """
        Retrieves or creates a cart.
        - If user is authenticated, finds or creates Cart(user=user).
          If a cart_token was provided (from guest browsing), merges guest cart into user's cart.
        - If user is None, finds or creates Cart with the specified cart_token.
        """
        if user and user.is_authenticated:
            user_cart, _ = Cart.objects.get_or_create(user=user)
            # Merge guest cart if cart_token provided
            if cart_token:
                try:
                    val_token = uuid.UUID(str(cart_token)) if isinstance(cart_token, (str, uuid.UUID)) else cart_token
                    guest_cart = Cart.objects.filter(cart_token=val_token, user__isnull=True).first()
                    if guest_cart and guest_cart.id != user_cart.id:
                        CartService.merge_carts(source_cart=guest_cart, target_cart=user_cart)
                        user_cart.refresh_from_db()
                except Exception:
                    pass
            return user_cart

        if cart_token:
            try:
                val_token = uuid.UUID(str(cart_token)) if isinstance(cart_token, (str, uuid.UUID)) else cart_token
                cart = Cart.objects.filter(cart_token=val_token).first()
                if cart:
                    return cart
                return Cart.objects.create(user=None, cart_token=val_token)
            except (ValueError, TypeError):
                pass

        return Cart.objects.create(user=None)

    @staticmethod
    @transaction.atomic
    def merge_carts(source_cart, target_cart):
        """
        Merges items from source_cart into target_cart (matching by product and size),
        then deletes source_cart.
        """
        for item in source_cart.items.select_related("product").all():
            existing = target_cart.items.filter(product=item.product, size=item.size).first()
            max_qty = item.product.stock
            if item.size and item.product.sizes.exists():
                size_obj = item.product.sizes.filter(size=item.size).first()
                if size_obj:
                    max_qty = size_obj.stock

            if existing:
                combined_qty = min(existing.quantity + item.quantity, max_qty)
                existing.quantity = max(combined_qty, 1)
                existing.save(update_fields=["quantity", "updated_at"])
            else:
                CartItem.objects.create(
                    cart=target_cart,
                    product=item.product,
                    size=item.size,
                    quantity=min(item.quantity, max_qty),
                )
        source_cart.delete()
        target_cart.save()

    @staticmethod
    @transaction.atomic
    def add_item(cart, product_id, quantity=1, size=""):
        """
        Adds a product (and optional size) to the cart or increments its quantity.
        """
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        product = Product.objects.select_related("store").prefetch_related("sizes").filter(pk=product_id).first()
        if not product:
            raise ValidationError("Product not found.")

        if product.status != ProductStatus.ACTIVE:
            raise ValidationError("This product is currently inactive and cannot be added to cart.")

        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise ValidationError("This store is currently unavailable.")

        clean_size = str(size or "").strip().upper()

        # Handle size selection and stock
        available_stock = product.stock
        if product.sizes.exists():
            if not clean_size:
                # If sizes exist but user didn't specify, default to first in-stock size if available
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

        cart_item = cart.items.filter(product=product, size=clean_size).first()
        current_quantity = cart_item.quantity if cart_item else 0
        target_quantity = current_quantity + quantity

        if target_quantity > available_stock:
            size_note = f" in size '{clean_size}'" if clean_size else ""
            raise ValidationError(
                f"Cannot add {quantity} item(s). Only {available_stock - current_quantity} more item(s) available{size_note}."
            )

        if cart_item:
            cart_item.quantity = target_quantity
            cart_item.save(update_fields=["quantity", "updated_at"])
        else:
            cart_item = CartItem.objects.create(
                cart=cart,
                product=product,
                size=clean_size,
                quantity=target_quantity,
            )

        cart.save(update_fields=["updated_at"])
        return cart_item

    @staticmethod
    @transaction.atomic
    def update_item_quantity(cart, item_id, quantity):
        """
        Updates the quantity of a cart item.
        If quantity is 0, removes the item.
        """
        cart_item = cart.items.select_related("product").prefetch_related("product__sizes").filter(pk=item_id).first()
        if not cart_item:
            raise ValidationError("Item not found in cart.")

        if quantity <= 0:
            cart_item.delete()
            cart.save(update_fields=["updated_at"])
            return None

        product = cart_item.product
        max_stock = product.stock
        if cart_item.size and product.sizes.exists():
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
        return cart_item

    @staticmethod
    def remove_item(cart, item_id):
        """
        Removes an item from the cart.
        """
        cart_item = cart.items.filter(pk=item_id).first()
        if not cart_item:
            raise ValidationError("Item not found in cart.")
        cart_item.delete()
        cart.save(update_fields=["updated_at"])

    @staticmethod
    def clear_cart(cart):
        """
        Removes all items from the cart.
        """
        cart.items.all().delete()
        cart.save(update_fields=["updated_at"])

    @staticmethod
    def validate_cart(cart):
        """
        Validates all items in the cart against current stock and availability.
        Returns a dictionary with summary, valid flag, and list of issues.
        """
        issues = []
        is_valid = True

        for item in cart.items.select_related("product", "product__store").prefetch_related("product__sizes").all():
            product = item.product
            store = product.store

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
            else:
                max_stock = product.stock
                if item.size and product.sizes.exists():
                    size_obj = product.sizes.filter(size=item.size).first()
                    max_stock = size_obj.stock if size_obj else 0

                if max_stock < item.quantity:
                    is_valid = False
                    size_str = f" in size '{item.size}'" if item.size else ""
                    issues.append({
                        "item_id": item.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "size": item.size,
                        "issue": f"Requested {item.quantity}, but only {max_stock} available{size_str}.",
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
        Converts all items in the cart into one or more confirmed orders grouped by store.
        Skips payment gateway capture for PoC, advancing orders directly to CONFIRMED.
        Empties the cart upon successful order placement.
        """
        if not user or not user.is_authenticated:
            raise ValidationError("An authenticated user account is required to complete checkout.")

        items = list(cart.items.select_related("product", "product__store").prefetch_related("product__sizes").all())
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
        from orders.models import OrderStatus

        for store, store_items in items_by_store.items():
            items_payload = [
                {
                    "product_id": item.product.id,
                    "quantity": item.quantity,
                    "size": item.size,
                }
                for item in store_items
            ]

            order = OrderService.create_order(
                store=store,
                customer_data=customer_data,
                items_data=items_payload,
                user=user,
            )

            # Direct confirmation for PoC (skipping payment gateway capture)
            OrderService.update_order_status(
                order=order,
                new_status=OrderStatus.CONFIRMED,
                user=user,
                notes="Order placed and confirmed directly (PoC checkout).",
            )

            created_orders.append(order)

        # Clear items from cart
        cart.items.all().delete()
        cart.save(update_fields=["updated_at"])

        return created_orders
