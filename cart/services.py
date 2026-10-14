import uuid
from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Product, ProductStatus
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
        Merges items from source_cart into target_cart, then deletes source_cart.
        """
        for item in source_cart.items.select_related("product").all():
            existing = target_cart.items.filter(product=item.product).first()
            if existing:
                max_qty = item.product.stock
                combined_qty = min(existing.quantity + item.quantity, max_qty)
                existing.quantity = max(combined_qty, 1)
                existing.save(update_fields=["quantity", "updated_at"])
            else:
                CartItem.objects.create(
                    cart=target_cart,
                    product=item.product,
                    quantity=min(item.quantity, item.product.stock),
                )
        source_cart.delete()
        target_cart.save()

    @staticmethod
    @transaction.atomic
    def add_item(cart, product_id, quantity=1):
        """
        Adds a product to the cart or increments its quantity.
        """
        if quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        product = Product.objects.select_related("store").filter(pk=product_id).first()
        if not product:
            raise ValidationError("Product not found.")

        if product.status != ProductStatus.ACTIVE:
            raise ValidationError("This product is currently inactive and cannot be added to cart.")

        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise ValidationError("This store is currently unavailable.")

        if product.stock <= 0:
            raise ValidationError("This product is currently out of stock.")

        cart_item = cart.items.filter(product=product).first()
        current_quantity = cart_item.quantity if cart_item else 0
        target_quantity = current_quantity + quantity

        if target_quantity > product.stock:
            raise ValidationError(
                f"Cannot add {quantity} item(s). Only {product.stock - current_quantity} more item(s) available in stock."
            )

        if cart_item:
            cart_item.quantity = target_quantity
            cart_item.save(update_fields=["quantity", "updated_at"])
        else:
            cart_item = CartItem.objects.create(
                cart=cart,
                product=product,
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
        cart_item = cart.items.select_related("product").filter(pk=item_id).first()
        if not cart_item:
            raise ValidationError("Item not found in cart.")

        if quantity <= 0:
            cart_item.delete()
            cart.save(update_fields=["updated_at"])
            return None

        product = cart_item.product
        if quantity > product.stock:
            raise ValidationError(
                f"Cannot set quantity to {quantity}. Only {product.stock} item(s) available in stock."
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

        for item in cart.items.select_related("product", "product__store").all():
            product = item.product
            store = product.store

            if store.status != StoreStatus.ACTIVE or store.archived_at is not None:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "issue": "Store is unavailable.",
                    "code": "STORE_UNAVAILABLE",
                })
            elif product.status != ProductStatus.ACTIVE:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "issue": "Product is no longer active.",
                    "code": "PRODUCT_INACTIVE",
                })
            elif product.stock < item.quantity:
                is_valid = False
                issues.append({
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "issue": f"Requested {item.quantity}, but only {product.stock} available in stock.",
                    "code": "INSUFFICIENT_STOCK",
                    "available_stock": product.stock,
                })

        return {
            "is_valid": is_valid,
            "issues_count": len(issues),
            "issues": issues,
            "total_items": cart.total_items,
            "subtotal": float(cart.subtotal),
            "currency": cart.currency,
        }
