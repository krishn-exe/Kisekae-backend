import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'KiseKaeDjango.settings')

app = Celery('KiseKaeDjango')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls flush_expired_tokens_task every day (86400 seconds)
    sender.add_periodic_task(86400.0, 'accounts.tasks.flush_expired_tokens_task', name='flush expired tokens daily')

    # Nightly refresh of user taste profiles for recommendation ML (every 24 hours)
    sender.add_periodic_task(86400.0, 'products.tasks.refresh_all_taste_profiles_task', name='refresh user taste profiles daily')

    # Expire stale store invitations hourly (3600 seconds)
    sender.add_periodic_task(3600.0, 'stores.tasks.expire_stale_store_invitations_task', name='expire stale store invitations hourly')

    # Expire unpaid orders with elapsed 5-minute reservation window (every 60 seconds)
    sender.add_periodic_task(60.0, 'orders.tasks.expire_stale_pending_orders_task', name='expire stale pending orders every minute')
