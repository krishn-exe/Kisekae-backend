from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Product, ProductCategory, ProductStatus
from stores.models import StoreStatus

from .models import Combo, ComboItem, TRYON_SUPPORTED_CATEGORIES, TRYON_UNSUPPORTED_CATEGORIES


class ComboValidationService:
    """
    Centralized validation rules for Combos and Virtual Try-On outfit composition.
    Enforces category constraints, compatibility rules, and product status checks.
    """

    @classmethod
    def validate_product_eligibility(cls, product: Product) -> None:
        """Validates that a single product is active and in an eligible try-on category."""
        if not product:
            raise ValidationError("Product not found.", code="PRODUCT_NOT_FOUND")

        if product.status != ProductStatus.ACTIVE:
            raise ValidationError(
                f"Product '{product.name}' is not active and cannot be added to a combo.",
                code="PRODUCT_INACTIVE",
            )

        if product.store.status != StoreStatus.ACTIVE or product.store.archived_at is not None:
            raise ValidationError(
                f"Store for product '{product.name}' is not active.",
                code="STORE_INACTIVE",
            )

        if product.category in TRYON_UNSUPPORTED_CATEGORIES or product.category not in TRYON_SUPPORTED_CATEGORIES:
            display_name = product.get_category_display() if hasattr(product, "get_category_display") else product.category
            raise ValidationError(
                f"Category '{display_name}' is not supported for Combos and Virtual Try-On. "
                f"Allowed categories: Top, Bottom, One-Piece, Outerwear.",
                code="UNSUPPORTED_CATEGORY",
            )

    @classmethod
    def validate_category_composition(cls, categories: list[str]) -> None:
        """
        Validates outfit category compatibility rules:
        1. Only eligible categories (TOP, BOTTOM, ONE_PIECE, OUTERWEAR).
        2. At most one product per category.
        3. If ONE_PIECE is present, no TOP or BOTTOM allowed (and vice versa).
        4. OUTERWEAR can combine with anything.
        5. Max 3 items per combo.
        """
        if not categories:
            return

        cat_counts: dict[str, int] = {}
        for cat in categories:
            if cat in TRYON_UNSUPPORTED_CATEGORIES or cat not in TRYON_SUPPORTED_CATEGORIES:
                raise ValidationError(
                    f"Category '{cat}' is not supported for Combos. "
                    f"Allowed categories: {', '.join(sorted(TRYON_SUPPORTED_CATEGORIES))}.",
                    code="UNSUPPORTED_CATEGORY",
                )
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Rule 1: Max one per category
        for cat, count in cat_counts.items():
            if count > 1:
                raise ValidationError(
                    f"A combo cannot contain multiple items of category '{cat}'. Found {count}.",
                    code="DUPLICATE_CATEGORY",
                )

        has_one_piece = ProductCategory.ONE_PIECE in cat_counts
        has_top = ProductCategory.TOP in cat_counts
        has_bottom = ProductCategory.BOTTOM in cat_counts

        # Rule 2: ONE_PIECE vs TOP/BOTTOM mutual exclusivity
        if has_one_piece and (has_top or has_bottom):
            conflicts = []
            if has_top:
                conflicts.append("Top")
            if has_bottom:
                conflicts.append("Bottom")
            raise ValidationError(
                f"A One-Piece cannot be combined with {' or '.join(conflicts)}. "
                "Choose either a One-Piece OR separate Top/Bottom garments.",
                code="ONE_PIECE_CONFLICT",
            )

        # Rule 3: Max outfit size
        if len(categories) > 3:
            raise ValidationError(
                "A combo cannot exceed 3 items (Top + Bottom + Outerwear, or One-Piece + Outerwear).",
                code="MAX_ITEMS_EXCEEDED",
            )

    @classmethod
    def validate_products(cls, product_ids: list[int]) -> list[Product]:
        """
        Validates a list of product IDs:
        - Resolves objects from DB in a single query
        - Validates uniqueness
        - Validates each product's eligibility
        - Validates overall outfit composition
        """
        if not product_ids:
            return []

        # Check for duplicate IDs in payload
        if len(product_ids) != len(set(product_ids)):
            raise ValidationError("Duplicate product IDs provided.", code="DUPLICATE_PRODUCT")

        products = list(
            Product.objects.filter(id__in=product_ids).select_related("store")
        )

        found_ids = {p.id for p in products}
        missing_ids = set(product_ids) - found_ids
        if missing_ids:
            raise ValidationError(
                f"Products with IDs {sorted(missing_ids)} do not exist.",
                code="PRODUCT_NOT_FOUND",
            )

        for product in products:
            cls.validate_product_eligibility(product)

        categories = [p.category for p in products]
        cls.validate_category_composition(categories)

        return products


class ComboService:
    """Business operations for creating, updating, and managing user Combos."""

    @staticmethod
    @transaction.atomic
    def create_combo(user, name: str = "", product_ids: list[int] = None) -> Combo:
        product_ids = product_ids or []
        products = ComboValidationService.validate_products(product_ids)

        clean_name = (name or "").strip() or "My Combo"
        combo = Combo.objects.create(user=user, name=clean_name)

        if products:
            items = [
                ComboItem(combo=combo, product=p, category=p.category)
                for p in products
            ]
            ComboItem.objects.bulk_create(items)

        return combo

    @staticmethod
    @transaction.atomic
    def update_combo(combo: Combo, name: str = None, product_ids: list[int] = None) -> Combo:
        if name is not None:
            combo.name = name.strip() or "My Combo"
            combo.save(update_fields=["name", "updated_at"])

        if product_ids is not None:
            products = ComboValidationService.validate_products(product_ids)
            combo.items.all().delete()
            if products:
                items = [
                    ComboItem(combo=combo, product=p, category=p.category)
                    for p in products
                ]
                ComboItem.objects.bulk_create(items)
            combo.save(update_fields=["updated_at"])

        return combo

    @staticmethod
    @transaction.atomic
    def add_item_to_combo(combo: Combo, product_id: int) -> ComboItem:
        product = Product.objects.filter(id=product_id).select_related("store").first()
        if not product:
            raise ValidationError("Product not found.", code="PRODUCT_NOT_FOUND")

        ComboValidationService.validate_product_eligibility(product)

        if combo.items.filter(product=product).exists():
            raise ValidationError("This product is already in the combo.", code="PRODUCT_ALREADY_IN_COMBO")

        current_categories = list(combo.items.values_list("category", flat=True))
        projected_categories = current_categories + [product.category]

        ComboValidationService.validate_category_composition(projected_categories)

        return ComboItem.objects.create(
            combo=combo,
            product=product,
            category=product.category,
        )

    @staticmethod
    @transaction.atomic
    def remove_item_from_combo(combo: Combo, item_id: int) -> bool:
        deleted_count, _ = combo.items.filter(id=item_id).delete()
        if deleted_count == 0:
            raise ValidationError("Combo item not found.", code="ITEM_NOT_FOUND")
        combo.save(update_fields=["updated_at"])
        return True

    @staticmethod
    @transaction.atomic
    def delete_combo(combo: Combo) -> bool:
        combo.delete()
        return True
