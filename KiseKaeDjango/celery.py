import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'KiseKaeDjango.settings')

app = Celery('KiseKaeDjango')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

@app.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls flush_expired_tokens_task every day at midnight (or periodically)
    # We can schedule it to run every 24 hours (86400 seconds)
    sender.add_periodic_task(86400.0, 'accounts.tasks.flush_expired_tokens_task', name='flush expired tokens daily')