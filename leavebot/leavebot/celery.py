# leavebot/leavebot/celery.py

"""
Celery Configuration for the Leavebot Project.

This module is the entry point for the Celery distributed task queue. It defines
the Celery application instance and configures it to work seamlessly with the
Django project.

When a Celery worker is started, this file is executed to:
1.  Ensure the Django settings are loaded correctly.
2.  Create and configure the Celery app instance.
3.  Automatically discover asynchronous tasks defined in the project's apps.
"""

import os
from celery import Celery

# --- Django Integration ---
# This line is crucial. It sets the default Django settings module for the 'celery'
# command-line program. It must come before the app instance is created.
# This ensures that the Celery workers run with the same Django project context
# as the main web application.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leavebot.settings')

# --- Celery Application Instance ---
# Here we create the instance of the Celery application.
# The first argument, 'leavebot', is the name of the current project.
app = Celery('leavebot')

# --- Configuration ---
# This method loads the Celery configuration from the Django settings file.
# The `namespace='CELERY'` argument means that all Celery-related configuration
# settings in your `settings.py` file must be prefixed with 'CELERY_'.
# For example: CELERY_BROKER_URL, CELERY_RESULT_BACKEND.
# This is a best practice for keeping Celery settings organized and distinct.
app.config_from_object('django.conf:settings', namespace='CELERY')

# --- Task Discovery ---
# This command tells Celery to automatically look for task modules in all
# registered Django applications. Celery will search for a file named `tasks.py`
# in each app and automatically register any tasks defined within it.
# This allows for clean separation of tasks into their respective apps.
app.autodiscover_tasks()