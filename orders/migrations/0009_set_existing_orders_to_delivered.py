from django.db import migrations
from django.utils import timezone


def migrate_existing_orders_to_delivered(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    OrderStatusHistory = apps.get_model("orders", "OrderStatusHistory")
    now = timezone.now()

    # Transition all existing orders not already in DELIVERED status
    orders_to_update = Order.objects.exclude(status="DELIVERED")
    for order in orders_to_update:
        old_status = order.status
        order.status = "DELIVERED"
        if not order.delivered_at:
            order.delivered_at = getattr(order, "created_at", None) or now
        order.save(update_fields=["status", "delivered_at"])

        OrderStatusHistory.objects.create(
            order=order,
            from_status=old_status,
            to_status="DELIVERED",
            notes="Data Migration: Existing order status transitioned to DELIVERED.",
        )


def reverse_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0008_alter_order_status"),
    ]

    operations = [
        migrations.RunPython(
            migrate_existing_orders_to_delivered,
            reverse_code=reverse_migration,
        ),
    ]
