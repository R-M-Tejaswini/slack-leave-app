# leavebot/leavebot/celery.py
import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leavebot.settings')

# Create the Celery application instance.
app = Celery('leavebot')

# Load configuration from Django settings, using a 'CELERY_' namespace.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Automatically discover and load tasks from all registered Django apps.
app.autodiscover_tasks()