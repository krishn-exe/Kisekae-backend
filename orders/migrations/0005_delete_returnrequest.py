
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0004_orderitem_size'),
    ]

    operations = [
        migrations.DeleteModel(
            name='ReturnRequest',
        ),
    ]
