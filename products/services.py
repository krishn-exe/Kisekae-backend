from django.core.exceptions import ValidationError
from django.db import transaction

from stores.models import StoreStatus

from .models import Product, ProductImage, ProductSize, ProductStatus
from .tasks import generate_product_embedding_task

MAX_IMAGES_PER_PRODUCT = 10


class ProductService:
    @staticmethod
    @transaction.atomic
    def create_product(store, user, **data):
        if store.status != StoreStatus.ACTIVE:
            raise ValidationError(
                f"Cannot add products while the store is {store.status.lower()}."
            )

        sizes_data = data.pop("sizes", None)

        # If sizes were provided, derive total stock from sum of size stocks
        if sizes_data:
            data["stock"] = sum(item.get("stock", 0) for item in sizes_data)

        product = Product.objects.create(
            store=store,
            created_by=user,
            **data,
        )

        if sizes_data:
            size_objs = [
                ProductSize(
                    product=product,
                    size=item["size"].strip().upper(),
                    stock=item.get("stock", 0),
                )
                for item in sizes_data
            ]
            for obj in size_objs:
                obj.full_clean()
            ProductSize.objects.bulk_create(size_objs)

        transaction.on_commit(lambda: generate_product_embedding_task.delay(product.id))
        return product

    @staticmethod
    @transaction.atomic
    def update_product(product, **data):
        sizes_data = data.pop("sizes", None)
        update_fields = []

        if sizes_data is not None:
            # Overwrite sizes with new size configuration
            product.sizes.all().delete()
            size_objs = [
                ProductSize(
                    product=product,
                    size=item["size"].strip().upper(),
                    stock=item.get("stock", 0),
                )
                for item in sizes_data
            ]
            for obj in size_objs:
                obj.full_clean()
            ProductSize.objects.bulk_create(size_objs)
            product.stock = sum(item.get("stock", 0) for item in sizes_data)
            update_fields.append("stock")

        for field, value in data.items():
            if hasattr(product, field):
                setattr(product, field, value)
                update_fields.append(field)

        if update_fields:
            if "name" in update_fields:
                product.slug = ""  # Re-generated in save()
                update_fields.append("slug")
            update_fields.append("updated_at")
            product.save(update_fields=list(set(update_fields)))

            if any(f in update_fields for f in ["name", "description", "category", "color"]):
                transaction.on_commit(lambda: generate_product_embedding_task.delay(product.id))

        return product

    @staticmethod
    @transaction.atomic
    def delete_product(product):
        # Clean up legacy image
        if product.image:
            product.image.delete(save=False)
        # Clean up gallery images
        for img in product.images.all():
            if img.image:
                img.image.delete(save=False)
        product.delete()
        return True

    @staticmethod
    def set_product_image(product, image_file):
        """Legacy helper: sets/replaces primary image and ensures it's in images gallery."""
        if product.image:
            product.image.delete(save=False)
        product.image = image_file
        product.save(update_fields=["image", "updated_at"])

        # Also add or update order 0 in ProductImage gallery if under limit
        first_img = product.images.order_by("order").first()
        if first_img:
            if first_img.image:
                first_img.image.delete(save=False)
            first_img.image = image_file
            first_img.save(update_fields=["image"])
        elif product.images.count() < MAX_IMAGES_PER_PRODUCT:
            ProductImage.objects.create(product=product, image=image_file, order=0)

        return product

    @staticmethod
    def remove_product_image(product):
        """Legacy helper: removes primary image."""
        if product.image:
            product.image.delete(save=False)
        product.image = None
        product.save(update_fields=["image", "updated_at"])
        return product

    @staticmethod
    @transaction.atomic
    def add_gallery_image(product, image_file, order=None):
        current_count = product.images.count()
        if current_count >= MAX_IMAGES_PER_PRODUCT:
            raise ValidationError(
                f"Maximum of {MAX_IMAGES_PER_PRODUCT} images allowed per product. Currently has {current_count}."
            )

        if order is None:
            order = current_count

        img_obj = ProductImage.objects.create(
            product=product,
            image=image_file,
            order=order,
        )

        # If primary image is empty, populate it as thumbnail
        if not product.image:
            product.image = image_file
            product.save(update_fields=["image", "updated_at"])

        return img_obj

    @staticmethod
    @transaction.atomic
    def remove_gallery_image(product, image_id):
        img_obj = product.images.filter(pk=image_id).first()
        if not img_obj:
            raise ValidationError("Product image not found.")

        if img_obj.image:
            img_obj.image.delete(save=False)
        img_obj.delete()

        # Re-sync primary thumbnail if needed
        remaining = product.images.order_by("order", "id").first()
        if remaining and remaining.image:
            product.image = remaining.image
        else:
            product.image = None
        product.save(update_fields=["image", "updated_at"])

        return True
