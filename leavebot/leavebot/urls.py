# leavebot/leavebot/urls.py

"""
Root URL Configuration for the Leavebot Project.

This module is the primary URL dispatcher for the entire Django project. It acts
as a table of contents for the site's URLs, routing incoming requests to the
appropriate application's URL configuration.

The defined patterns are:
- `/admin/`: Routes to the built-in Django administration site.
- `/slack/`: Delegates all Slack-related webhook endpoints to the `slackapp`.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # This path includes the URLs for the Django admin interface, a powerful
    # tool for managing the application's data models (e.g., Employees, Teams).
    path('admin/', admin.site.urls),

    # This line is key for app organization. It delegates any URL that starts
    # with `slack/` to be handled by the `urls.py` file within the `slackapp`.
    # This keeps the project modular and makes the app reusable.
    # For example, a request to `/slack/commands/` will be routed to `slackapp.urls`.
    path('slack/', include('slackapp.urls')),
]