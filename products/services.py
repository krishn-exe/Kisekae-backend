from django.core.exceptions import ValidationError
from django.db import transaction

from stores.models import StoreStatus

from .models import Product, ProductStatus


class ProductService:
    @staticmethod
    @transaction.atomic
    def create_product(store, user, **data):
        if store.status != StoreStatus.ACTIVE:
            raise ValidationError(
                f"Cannot add products while the store is {store.status.lower()}."
            )

        product = Product.objects.create(
            store=store,
            created_by=user,
            **data,
        )
        return product

    @staticmethod
    def update_product(product, **data):
        update_fields = []
        for field, value in data.items():
            if hasattr(product, field):
                setattr(product, field, value)
                update_fields.append(field)
        if update_fields:
            # Re-generate slug if name changed
            if "name" in update_fields:
                product.slug = ""  # Will be auto-generated in save()
            update_fields.append("updated_at")
            product.save()
        return product

    @staticmethod
    def delete_product(product):
        if product.image:
            product.image.delete(save=False)
        product.delete()
        return True

    @staticmethod
    def set_product_image(product, image_file):
        if product.image:
            product.image.delete(save=False)
        product.image = image_file
        product.save(update_fields=["image", "updated_at"])
        return product

    @staticmethod
    def remove_product_image(product):
        if product.image:
            product.image.delete(save=False)
        product.image = None
        product.save(update_fields=["image", "updated_at"])
        return product
