
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0007_backfill_product_variants'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_refundable',
            field=models.BooleanField(default=True, help_text='Seller option: whether this product is eligible for return and refund post-delivery.'),
        ),
    ]
