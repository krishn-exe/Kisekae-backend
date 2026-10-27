from django.core.exceptions import ValidationError
from django.db import transaction

from stores.models import StoreStatus

from .models import (
    NO_SIZE_CATEGORIES,
    Product,
    ProductImage,
    ProductSize,
    ProductVariant,
)
from .tasks import generate_product_embedding_task

MAX_IMAGES_PER_PRODUCT = 10
MAX_IMAGES_PER_VARIANT = 10


class ProductService:
    @staticmethod
    @transaction.atomic
    def create_product(store, user, **data):
        if store.status != StoreStatus.ACTIVE:
            raise ValidationError(
                f"Cannot add products while the store is {store.status.lower()}."
            )

        variants_data = data.pop("variants", None)
        sizes_data = data.pop("sizes", None)
        color = data.get("color", "")

        product = Product.objects.create(
            store=store,
            created_by=user,
            **data,
        )

        if variants_data:
            for v_data in variants_data:
                sizes_list = v_data.pop("sizes", [])
                v_stock = v_data.pop("stock", 0)
                variant = ProductVariant.objects.create(product=product, **v_data)

                if sizes_list:
                    size_objs = [
                        ProductSize(
                            product=product,
                            variant=variant,
                            size=(s.get("size") or "").strip().upper(),
                            stock=s.get("stock", 0),
                        )
                        for s in sizes_list
                    ]
                    for obj in size_objs:
                        obj.full_clean()
                    ProductSize.objects.bulk_create(size_objs)
                elif v_stock > 0 or product.category in NO_SIZE_CATEGORIES:
                    s_obj = ProductSize(
                        product=product,
                        variant=variant,
                        size="",
                        stock=v_stock,
                    )
                    s_obj.full_clean()
                    s_obj.save()
        elif sizes_data:
            # Legacy sizes fallback: create default variant
            default_variant = ProductVariant.objects.create(
                product=product,
                color_name=color or "Standard",
                color_hex="#000000",
            )
            size_objs = [
                ProductSize(
                    product=product,
                    variant=default_variant,
                    size=(item.get("size") or "").strip().upper(),
                    stock=item.get("stock", 0),
                )
                for item in sizes_data
            ]
            for obj in size_objs:
                obj.full_clean()
            ProductSize.objects.bulk_create(size_objs)
        elif data.get("stock", 0) > 0:
            default_variant = ProductVariant.objects.create(
                product=product,
                color_name=color or "Standard",
                color_hex="#000000",
            )
            s_obj = ProductSize(
                product=product,
                variant=default_variant,
                size="",
                stock=data.get("stock", 0),
            )
            s_obj.full_clean()
            s_obj.save()

        product.sync_total_stock()

        transaction.on_commit(lambda: generate_product_embedding_task.delay(product.id))
        from tryon.tasks import preprocess_product_image_task
        transaction.on_commit(lambda: preprocess_product_image_task.delay(product.id))
        return product

    @staticmethod
    @transaction.atomic
    def update_product(product, **data):
        variants_data = data.pop("variants", None)
        sizes_data = data.pop("sizes", None)
        update_fields = []

        if variants_data is not None:
            # Safe in-place update/upsert for variants and sizes (BUG-03 fix)
            existing_variants = {v.color_name.lower(): v for v in product.variants.all()}
            for v_data in variants_data:
                c_name = v_data.get("color_name", "").strip()
                c_hex = v_data.get("color_hex", "#000000")
                sku = v_data.get("sku", "")
                is_active = v_data.get("is_active", True)
                sizes_list = v_data.get("sizes", [])
                v_stock = v_data.get("stock", 0)

                key = c_name.lower()
                if key in existing_variants:
                    variant = existing_variants[key]
                    variant.color_hex = c_hex
                    variant.sku = sku
                    variant.is_active = is_active
                    variant.save(update_fields=["color_hex", "sku", "is_active", "updated_at"])
                else:
                    variant = ProductVariant.objects.create(
                        product=product,
                        color_name=c_name,
                        color_hex=c_hex,
                        sku=sku,
                        is_active=is_active,
                    )

                if sizes_list:
                    for s_item in sizes_list:
                        s_name = (s_item.get("size") or "").strip().upper()
                        s_stock = s_item.get("stock", 0)
                        ProductSize.objects.update_or_create(
                            product=product,
                            variant=variant,
                            size=s_name,
                            defaults={"stock": s_stock},
                        )
                elif v_stock > 0 or product.category in NO_SIZE_CATEGORIES:
                    ProductSize.objects.update_or_create(
                        product=product,
                        variant=variant,
                        size="",
                        defaults={"stock": v_stock},
                    )

        elif sizes_data is not None:
            # Legacy size list update: upsert sizes
            primary_variant = product.variants.first()
            if not primary_variant:
                primary_variant = ProductVariant.objects.create(
                    product=product,
                    color_name=product.color or "Standard",
                    color_hex="#000000",
                )
            for item in sizes_data:
                s_name = (item.get("size") or "").strip().upper()
                s_stock = item.get("stock", 0)
                ProductSize.objects.update_or_create(
                    product=product,
                    variant=primary_variant,
                    size=s_name,
                    defaults={"stock": s_stock},
                )

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

        product.sync_total_stock()
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
    @transaction.atomic
    def add_variant_image(variant, image_file, order=None):
        """
        Uploads an image for a specific color variant (up to 10 images per variant).
        The image at order=0 serves as the primary thumbnail.
        """
        current_count = variant.images.count()
        if current_count >= MAX_IMAGES_PER_VARIANT:
            raise ValidationError(
                f"Maximum of {MAX_IMAGES_PER_VARIANT} images allowed per variant. Currently has {current_count}."
            )

        if order is None:
            order = current_count

        img_obj = ProductImage.objects.create(
            product=variant.product,
            variant=variant,
            image=image_file,
            order=order,
        )

        from tryon.tasks import preprocess_product_image_task
        transaction.on_commit(lambda: preprocess_product_image_task.delay(variant.product.id))
        return img_obj

    @staticmethod
    @transaction.atomic
    def remove_variant_image(variant, image_id):
        img_obj = variant.images.filter(pk=image_id).first()
        if not img_obj:
            raise ValidationError("Variant image not found.", code="IMAGE_NOT_FOUND")

        if img_obj.image:
            img_obj.image.delete(save=False)
        img_obj.delete()

        from tryon.tasks import preprocess_product_image_task
        transaction.on_commit(lambda: preprocess_product_image_task.delay(variant.product.id))
        return True

    @staticmethod
    def set_product_image(product, image_file):
        """Legacy helper: sets/replaces primary image and ensures it's in images gallery."""
        if product.image:
            product.image.delete(save=False)
        product.image = image_file
        product.save(update_fields=["image", "updated_at"])

        first_img = product.images.order_by("order").first()
        if first_img:
            if first_img.image:
                first_img.image.delete(save=False)
            first_img.image = image_file
            first_img.save(update_fields=["image"])
        elif product.images.count() < MAX_IMAGES_PER_PRODUCT:
            ProductImage.objects.create(product=product, image=image_file, order=0)

        from tryon.tasks import preprocess_product_image_task
        preprocess_product_image_task.delay(product.id)
        return product

    @staticmethod
    def remove_product_image(product):
        """Legacy helper: removes primary image."""
        if product.image:
            product.image.delete(save=False)
        product.image = None
        product.save(update_fields=["image", "updated_at"])
        from tryon.tasks import preprocess_product_image_task
        preprocess_product_image_task.delay(product.id)
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

        if not product.image:
            product.image = image_file
            product.save(update_fields=["image", "updated_at"])

        from tryon.tasks import preprocess_product_image_task
        transaction.on_commit(lambda: preprocess_product_image_task.delay(product.id))
        return img_obj

    @staticmethod
    @transaction.atomic
    def remove_gallery_image(product, image_id):
        img_obj = product.images.filter(pk=image_id).first()
        if not img_obj:
            raise ValidationError("Product image not found.", code="IMAGE_NOT_FOUND")

        if img_obj.image:
            img_obj.image.delete(save=False)
        img_obj.delete()

        remaining = product.images.order_by("order", "id").first()
        if remaining and remaining.image:
            product.image = remaining.image
        else:
            product.image = None
        product.save(update_fields=["image", "updated_at"])
        from tryon.tasks import preprocess_product_image_task
        transaction.on_commit(lambda: preprocess_product_image_task.delay(product.id))

        return True
