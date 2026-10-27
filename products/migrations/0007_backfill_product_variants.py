from django.db import migrations


def backfill_variants(apps, schema_editor):
    Product = apps.get_model('products', 'Product')
    ProductVariant = apps.get_model('products', 'ProductVariant')
    ProductImage = apps.get_model('products', 'ProductImage')
    ProductSize = apps.get_model('products', 'ProductSize')

    for product in Product.objects.all():
        variants = ProductVariant.objects.filter(product=product)
        if not variants.exists():
            color_name = (product.color or "Standard").strip()
            variant = ProductVariant.objects.create(
                product=product,
                color_name=color_name,
                color_hex="#000000",
                sku=product.sku or "",
                is_active=True,
            )
        else:
            variant = variants.first()

        ProductImage.objects.filter(product=product, variant__isnull=True).update(variant=variant)
        ProductSize.objects.filter(product=product, variant__isnull=True).update(variant=variant)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0006_productvariant_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_variants, noop),
    ]
