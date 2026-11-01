
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0003_order_delivered_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='size',
            field=models.CharField(blank=True, default='', help_text='Selected apparel/shoe size (e.g., S, M, L, XL or 6-10).', max_length=20),
        ),
    ]
