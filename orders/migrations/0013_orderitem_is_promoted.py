
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0012_order_coupon_discount_couponcode_order_coupon_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='is_promoted',
            field=models.BooleanField(default=False, help_text='Whether this purchased item was attributed to a sponsored ad.'),
        ),
    ]
