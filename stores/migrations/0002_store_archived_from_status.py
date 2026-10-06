from django.db import migrations, models


def mark_legacy_archives_suspended(apps, schema_editor):
    Store = apps.get_model("stores", "Store")
    Store.objects.filter(status="ARCHIVED", archived_from_status__isnull=True).update(
        archived_from_status="SUSPENDED"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="store",
            name="archived_from_status",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ACTIVE", "Active"),
                    ("INACTIVE", "Inactive"),
                    ("SUSPENDED", "Suspended"),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(mark_legacy_archives_suspended, migrations.RunPython.noop),
    ]
