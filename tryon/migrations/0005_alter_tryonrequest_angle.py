
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tryon', '0004_tryonrequest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tryonrequest',
            name='angle',
            field=models.CharField(choices=[('AUTO', 'Auto Detect'), ('FRONT', 'Front'), ('BACK', 'Back'), ('SIDE', 'Side')], default='AUTO', help_text='Requested perspective for try-on (AUTO, FRONT, BACK, SIDE). Defaults to AUTO.', max_length=10),
        ),
    ]
