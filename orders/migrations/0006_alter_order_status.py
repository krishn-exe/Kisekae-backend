
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_delete_returnrequest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(choices=[('PENDING', 'Pending'), ('CONFIRMED', 'Confirmed'), ('PROCESSING', 'Processing'), ('SHIPPED', 'Shipped'), ('DELIVERED', 'Delivered'), ('CANCELLED', 'Cancelled')], db_index=True, default='PENDING', max_length=30),
        ),
    ]
